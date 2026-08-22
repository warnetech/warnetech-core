"""In-process store. Zero setup, nothing survives a restart.

The default for tests and for single-process scripts.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from .base import ContextStore, matches_metadata
from .types import MemoryRecord, SearchHit, cosine_similarity


class InMemoryContextStore(ContextStore):
    """Dict-backed store with brute-force cosine search."""

    def __init__(self) -> None:
        # namespace -> record id -> record
        self._data: dict[str, dict[str, MemoryRecord]] = {}
        self._lock = asyncio.Lock()

    def _live(self, namespace: str) -> list[MemoryRecord]:
        now = time.time()
        return [r for r in self._data.get(namespace, {}).values() if not r.is_expired(now)]

    async def upsert(self, records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        async with self._lock:
            out = []
            for record in records:
                bucket = self._data.setdefault(record.namespace, {})
                if record.key is not None:
                    existing = next(
                        (r for r in bucket.values() if r.key == record.key), None
                    )
                    if existing is not None:
                        # Preserve identity and creation time across updates so
                        # references held elsewhere stay valid.
                        record.id = existing.id
                        record.created_at = existing.created_at
                record.updated_at = time.time()
                bucket[record.id] = record
                out.append(record)
            return out

    async def get(
        self, namespace: str, *, id: str | None = None, key: str | None = None
    ) -> MemoryRecord | None:
        if id is None and key is None:
            raise ValueError("get() needs either id or key")
        for record in self._live(namespace):
            if (id is not None and record.id == id) or (key is not None and record.key == key):
                return record
        return None

    async def search(
        self,
        namespaces: Sequence[str],
        embedding: list[float],
        *,
        limit: int = 5,
        min_score: float = 0.0,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for namespace in namespaces:
            for record in self._live(namespace):
                if record.embedding is None:
                    continue
                if not matches_metadata(record, metadata_filter):
                    continue
                score = cosine_similarity(embedding, record.embedding)
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
        records = [r for r in self._live(namespace) if matches_metadata(r, metadata_filter)]
        records.sort(key=lambda r: r.updated_at, reverse=True)
        return records[offset : offset + limit]

    async def delete(
        self, namespace: str, *, id: str | None = None, key: str | None = None
    ) -> int:
        if id is None and key is None:
            raise ValueError("delete() needs either id or key")
        async with self._lock:
            bucket = self._data.get(namespace, {})
            doomed = [
                r.id
                for r in bucket.values()
                if (id is not None and r.id == id) or (key is not None and r.key == key)
            ]
            for record_id in doomed:
                del bucket[record_id]
            return len(doomed)

    async def clear(self, namespace: str) -> int:
        async with self._lock:
            return len(self._data.pop(namespace, {}))

    async def count(self, namespace: str) -> int:
        return len(self._live(namespace))

    async def namespaces(self) -> list[str]:
        return sorted(ns for ns, bucket in self._data.items() if bucket)

    async def purge_expired(self) -> int:
        now = time.time()
        async with self._lock:
            removed = 0
            for bucket in self._data.values():
                doomed = [rid for rid, r in bucket.items() if r.is_expired(now)]
                for rid in doomed:
                    del bucket[rid]
                removed += len(doomed)
            return removed
