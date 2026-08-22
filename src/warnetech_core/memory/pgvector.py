"""Postgres + pgvector context store.

The production backend for when brute-force scoring over SQLite stops being
instant. The interface is identical to
:class:`~warnetech_core.memory.sqlite.SQLiteContextStore`, so swapping is a
one-line change at construction and nothing downstream moves.

Requires the optional extra::

    pip install "warnetech-core[pgvector]"

and the ``vector`` extension available in the target database.

Note:
    Vector dimensionality is fixed per table at creation time (``dimensions``).
    Changing embedding models means a new table or a migration — the same
    constraint pgvector itself imposes.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

from ..errors import ConfigError, MemoryError_
from .base import ContextStore, matches_metadata
from .types import MemoryRecord, SearchHit


def _to_vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text input format: ``[1,2,3]``."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


class PgVectorContextStore(ContextStore):
    """Async Postgres store using ``pgvector`` for similarity search."""

    def __init__(
        self,
        dsn: str,
        *,
        table: str = "warnetech_memory",
        dimensions: int = 1536,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
    ) -> None:
        try:
            from psycopg_pool import AsyncConnectionPool
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ConfigError(
                "PgVectorContextStore needs the `pgvector` extra: "
                'pip install "warnetech-core[pgvector]"'
            ) from exc

        if not table.replace("_", "").isalnum():
            # The table name is interpolated into DDL/DML below, so it must not
            # be attacker-controlled or quotable.
            raise ConfigError(f"unsafe table name: {table!r}")

        self.table = table
        self.dimensions = dimensions
        self._pool = AsyncConnectionPool(
            dsn, min_size=min_pool_size, max_size=max_pool_size, open=False
        )
        self._ready = False

    async def _ensure_schema(self) -> None:
        if self._ready:
            return
        if self._pool.closed:
            await self._pool.open()
        async with self._pool.connection() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    id TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    key TEXT,
                    text TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding vector({self.dimensions}),
                    created_at DOUBLE PRECISION NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL,
                    expires_at DOUBLE PRECISION,
                    PRIMARY KEY (namespace, id)
                )
                """
            )
            await conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {self.table}_ns_key "
                f"ON {self.table} (namespace, key) WHERE key IS NOT NULL"
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_ns_updated "
                f"ON {self.table} (namespace, updated_at DESC)"
            )
            # Cosine-distance ANN index. Build it after the table has data for a
            # better-quality index; creating it empty is still correct.
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_embedding_cos "
                f"ON {self.table} USING hnsw (embedding vector_cosine_ops)"
            )
        self._ready = True

    @staticmethod
    def _row_to_record(row: tuple[Any, ...]) -> MemoryRecord:
        metadata = row[4]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return MemoryRecord(
            id=row[0],
            namespace=row[1],
            key=row[2],
            text=row[3],
            metadata=metadata or {},
            created_at=row[5],
            updated_at=row[6],
            expires_at=row[7],
        )

    _COLUMNS = "id, namespace, key, text, metadata, created_at, updated_at, expires_at"

    async def upsert(self, records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        await self._ensure_schema()
        now = time.time()
        async with self._pool.connection() as conn:
            for record in records:
                if record.embedding is not None and len(record.embedding) != self.dimensions:
                    raise MemoryError_(
                        f"embedding has {len(record.embedding)} dimensions but table "
                        f"{self.table!r} expects {self.dimensions}"
                    )
                if record.key is not None:
                    existing = await (
                        await conn.execute(
                            f"SELECT id, created_at FROM {self.table} "
                            f"WHERE namespace = %s AND key = %s",
                            (record.namespace, record.key),
                        )
                    ).fetchone()
                    if existing is not None:
                        record.id, record.created_at = existing[0], existing[1]
                record.updated_at = now
                await conn.execute(
                    f"""
                    INSERT INTO {self.table} (
                        id, namespace, key, text, metadata, embedding,
                        created_at, updated_at, expires_at
                    ) VALUES (%s,%s,%s,%s,%s::jsonb,%s::vector,%s,%s,%s)
                    ON CONFLICT (namespace, id) DO UPDATE SET
                        key = EXCLUDED.key,
                        text = EXCLUDED.text,
                        metadata = EXCLUDED.metadata,
                        embedding = EXCLUDED.embedding,
                        updated_at = EXCLUDED.updated_at,
                        expires_at = EXCLUDED.expires_at
                    """,
                    (
                        record.id,
                        record.namespace,
                        record.key,
                        record.text,
                        json.dumps(record.metadata or {}),
                        _to_vector_literal(record.embedding) if record.embedding else None,
                        record.created_at,
                        record.updated_at,
                        record.expires_at,
                    ),
                )
        return list(records)

    async def get(
        self, namespace: str, *, id: str | None = None, key: str | None = None
    ) -> MemoryRecord | None:
        if id is None and key is None:
            raise ValueError("get() needs either id or key")
        await self._ensure_schema()
        column, value = ("id", id) if id is not None else ("key", key)
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    SELECT {self._COLUMNS} FROM {self.table}
                    WHERE namespace = %s AND {column} = %s
                      AND (expires_at IS NULL OR expires_at > %s)
                    """,
                    (namespace, value, time.time()),
                )
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
    ) -> list[SearchHit]:
        if not namespaces:
            return []
        await self._ensure_schema()
        # `<=>` is cosine distance in pgvector, so similarity is 1 - distance.
        # Over-fetch when filtering in Python so the metadata filter does not
        # eat into the requested limit.
        fetch = limit * 4 if metadata_filter else limit
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    f"""
                    SELECT {self._COLUMNS}, 1 - (embedding <=> %s::vector) AS score
                    FROM {self.table}
                    WHERE namespace = ANY(%s)
                      AND embedding IS NOT NULL
                      AND (expires_at IS NULL OR expires_at > %s)
                      AND 1 - (embedding <=> %s::vector) >= %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (
                        _to_vector_literal(embedding),
                        list(namespaces),
                        time.time(),
                        _to_vector_literal(embedding),
                        min_score,
                        _to_vector_literal(embedding),
                        fetch,
                    ),
                )
            ).fetchall()

        hits = []
        for row in rows:
            record = self._row_to_record(row)
            if not matches_metadata(record, metadata_filter):
                continue
            hits.append(SearchHit(record=record, score=float(row[8])))
        return hits[:limit]

    async def list(
        self,
        namespace: str,
        *,
        limit: int = 100,
        offset: int = 0,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        await self._ensure_schema()
        params: list[Any] = [namespace, time.time()]
        clause = ""
        if metadata_filter:
            # Only plain scalar equality pushes down to JSONB containment; the
            # "any of these" list form is filtered in Python below.
            scalars = {
                k: v
                for k, v in metadata_filter.items()
                if not isinstance(v, (list, tuple, set))
            }
            if scalars:
                clause = "AND metadata @> %s::jsonb"
                params.append(json.dumps(scalars))
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    f"""
                    SELECT {self._COLUMNS} FROM {self.table}
                    WHERE namespace = %s AND (expires_at IS NULL OR expires_at > %s) {clause}
                    ORDER BY updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (*params, limit, offset),
                )
            ).fetchall()
        records = [self._row_to_record(r) for r in rows]
        return [r for r in records if matches_metadata(r, metadata_filter)]

    async def delete(
        self, namespace: str, *, id: str | None = None, key: str | None = None
    ) -> int:
        if id is None and key is None:
            raise ValueError("delete() needs either id or key")
        await self._ensure_schema()
        column, value = ("id", id) if id is not None else ("key", key)
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table} WHERE namespace = %s AND {column} = %s",
                (namespace, value),
            )
            return cursor.rowcount

    async def clear(self, namespace: str) -> int:
        await self._ensure_schema()
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table} WHERE namespace = %s", (namespace,)
            )
            return cursor.rowcount

    async def count(self, namespace: str) -> int:
        await self._ensure_schema()
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    SELECT COUNT(*) FROM {self.table}
                    WHERE namespace = %s AND (expires_at IS NULL OR expires_at > %s)
                    """,
                    (namespace, time.time()),
                )
            ).fetchone()
        return int(row[0])

    async def namespaces(self) -> list[str]:
        await self._ensure_schema()
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    f"SELECT DISTINCT namespace FROM {self.table} ORDER BY namespace"
                )
            ).fetchall()
        return [r[0] for r in rows]

    async def purge_expired(self) -> int:
        await self._ensure_schema()
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table} WHERE expires_at IS NOT NULL AND expires_at <= %s",
                (time.time(),),
            )
            return cursor.rowcount

    async def aclose(self) -> None:
        if not self._pool.closed:
            await self._pool.close()
