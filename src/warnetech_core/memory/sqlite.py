"""SQLite-backed context store — the default for real projects.

Durable, needs no server, and handles the scale a hub app actually has. Vectors
are stored as float32 blobs and scored in Python: brute force over a few tens of
thousands of records is milliseconds, and it keeps the dependency list at zero.
Past that, switch to :class:`~warnetech_core.memory.pgvector.PgVectorContextStore`
— the interface is identical, so nothing downstream changes.
"""

from __future__ import annotations

import array
import asyncio
import json
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .base import ContextStore, matches_metadata
from .types import MemoryRecord, SearchHit, cosine_similarity

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    key TEXT,
    text TEXT NOT NULL,
    metadata TEXT,
    embedding BLOB,
    dimensions INTEGER,
    embed_model TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL,
    PRIMARY KEY (namespace, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS memory_ns_key ON memory (namespace, key)
    WHERE key IS NOT NULL;
CREATE INDEX IF NOT EXISTS memory_ns_updated ON memory (namespace, updated_at);
CREATE INDEX IF NOT EXISTS memory_expires ON memory (expires_at)
    WHERE expires_at IS NOT NULL;
"""


def _pack(vector: list[float] | None) -> bytes | None:
    if vector is None:
        return None
    return array.array("f", vector).tobytes()


def _unpack(blob: bytes | None) -> list[float] | None:
    if blob is None:
        return None
    buf = array.array("f")
    buf.frombytes(blob)
    return list(buf)


class SQLiteContextStore(ContextStore):
    """Durable store with no server to run."""

    def __init__(self, path: str | Path = "warnetech_memory.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # SQLite calls are quick but blocking; serialise them behind one lock
        # so concurrent coroutines cannot interleave mid-transaction.
        self._lock = asyncio.Lock()

    # ---- row mapping ----------------------------------------------------

    @staticmethod
    def _row_to_record(row: sqlite3.Row, *, with_embedding: bool = True) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            namespace=row["namespace"],
            key=row["key"],
            text=row["text"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            embedding=_unpack(row["embedding"]) if with_embedding else None,
            embed_model=row["embed_model"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        )

    # ---- writes ---------------------------------------------------------

    async def upsert(self, records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        async with self._lock:
            now = time.time()
            out = []
            for record in records:
                if record.key is not None:
                    existing = self._conn.execute(
                        "SELECT id, created_at FROM memory WHERE namespace = ? AND key = ?",
                        (record.namespace, record.key),
                    ).fetchone()
                    if existing is not None:
                        record.id = existing["id"]
                        record.created_at = existing["created_at"]
                record.updated_at = now
                self._conn.execute(
                    """
                    INSERT INTO memory (
                        id, namespace, key, text, metadata, embedding, dimensions,
                        embed_model, created_at, updated_at, expires_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (namespace, id) DO UPDATE SET
                        key = excluded.key,
                        text = excluded.text,
                        metadata = excluded.metadata,
                        embedding = excluded.embedding,
                        dimensions = excluded.dimensions,
                        embed_model = excluded.embed_model,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        record.id,
                        record.namespace,
                        record.key,
                        record.text,
                        json.dumps(record.metadata) if record.metadata else None,
                        _pack(record.embedding),
                        len(record.embedding) if record.embedding else None,
                        record.embed_model,
                        record.created_at,
                        record.updated_at,
                        record.expires_at,
                    ),
                )
                out.append(record)
            self._conn.commit()
            return out

    async def delete(
        self, namespace: str, *, id: str | None = None, key: str | None = None
    ) -> int:
        if id is None and key is None:
            raise ValueError("delete() needs either id or key")
        column, value = ("id", id) if id is not None else ("key", key)
        async with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM memory WHERE namespace = ? AND {column} = ?", (namespace, value)
            )
            self._conn.commit()
            return cursor.rowcount

    async def clear(self, namespace: str) -> int:
        async with self._lock:
            cursor = self._conn.execute("DELETE FROM memory WHERE namespace = ?", (namespace,))
            self._conn.commit()
            return cursor.rowcount

    async def purge_expired(self) -> int:
        async with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM memory WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (time.time(),),
            )
            self._conn.commit()
            return cursor.rowcount

    # ---- reads ----------------------------------------------------------

    async def get(
        self, namespace: str, *, id: str | None = None, key: str | None = None
    ) -> MemoryRecord | None:
        if id is None and key is None:
            raise ValueError("get() needs either id or key")
        column, value = ("id", id) if id is not None else ("key", key)
        async with self._lock:
            row = self._conn.execute(
                f"""
                SELECT * FROM memory
                WHERE namespace = ? AND {column} = ?
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (namespace, value, time.time()),
            ).fetchone()
        return self._row_to_record(row) if row else None

    async def search(
        self,
        namespaces: Sequence[str],
        embedding: list[float],
        *,
        limit: int = 5,
        min_score: float = 0.0,
        metadata_filter: dict[str, Any] | None = None,
        embed_model: str | None = None,
    ) -> list[SearchHit]:
        if not namespaces:
            return []
        placeholders = ",".join("?" * len(namespaces))
        # Filtering on embed_model as well as dimensions matters: two models can
        # share a dimensionality while producing incomparable vectors.
        model_clause = "" if embed_model is None else "AND embed_model = ?"
        model_params: tuple = () if embed_model is None else (embed_model,)
        async with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM memory
                WHERE namespace IN ({placeholders})
                  AND embedding IS NOT NULL
                  AND dimensions = ?
                  {model_clause}
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (*namespaces, len(embedding), *model_params, time.time()),
            ).fetchall()

        hits: list[SearchHit] = []
        for row in rows:
            record = self._row_to_record(row)
            if not matches_metadata(record, metadata_filter):
                continue
            score = cosine_similarity(embedding, record.embedding or [])
            if score >= min_score:
                hits.append(SearchHit(record=record, score=score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    async def list(
        self,
        namespace: str,
        *,
        limit: int = 100,
        offset: int = 0,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        async with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM memory
                WHERE namespace = ? AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY updated_at DESC
                """,
                (namespace, time.time()),
            ).fetchall()
        records = [self._row_to_record(r) for r in rows]
        if metadata_filter:
            records = [r for r in records if matches_metadata(r, metadata_filter)]
        return records[offset : offset + limit]

    async def count(self, namespace: str) -> int:
        async with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS n FROM memory
                WHERE namespace = ? AND (expires_at IS NULL OR expires_at > ?)
                """,
                (namespace, time.time()),
            ).fetchone()
        return int(row["n"])

    async def namespaces(self) -> list[str]:
        async with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT namespace FROM memory ORDER BY namespace"
            ).fetchall()
        return [r["namespace"] for r in rows]

    async def aclose(self) -> None:
        async with self._lock:
            self._conn.close()
