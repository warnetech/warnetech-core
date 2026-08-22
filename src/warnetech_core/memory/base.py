"""Storage adapter interface for the memory layer.

Implement this to add a backend. Stores handle persistence and filtering only —
embedding, namespacing policy and context assembly live in
:class:`~warnetech_core.memory.service.MemoryService`, so every backend behaves
the same way.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from typing import Any

from .types import MemoryRecord, SearchHit


class ContextStore(abc.ABC):
    """Persistence for :class:`MemoryRecord`s.

    Implementations must:

    * treat ``namespace`` as a hard filter — never return a record from a
      namespace that was not asked for;
    * upsert on ``(namespace, key)`` when ``key`` is set, and on ``id`` otherwise;
    * exclude expired records from reads.
    """

    @abc.abstractmethod
    async def upsert(self, records: Sequence[MemoryRecord]) -> list[MemoryRecord]: ...

    @abc.abstractmethod
    async def get(
        self, namespace: str, *, id: str | None = None, key: str | None = None
    ) -> MemoryRecord | None: ...

    @abc.abstractmethod
    async def search(
        self,
        namespaces: Sequence[str],
        embedding: list[float],
        *,
        limit: int = 5,
        min_score: float = 0.0,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SearchHit]: ...

    @abc.abstractmethod
    async def list(
        self,
        namespace: str,
        *,
        limit: int = 100,
        offset: int = 0,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]: ...

    @abc.abstractmethod
    async def delete(
        self, namespace: str, *, id: str | None = None, key: str | None = None
    ) -> int: ...

    @abc.abstractmethod
    async def clear(self, namespace: str) -> int:
        """Drop every record in one namespace. Returns the number removed."""

    @abc.abstractmethod
    async def count(self, namespace: str) -> int: ...

    @abc.abstractmethod
    async def namespaces(self) -> list[str]: ...

    async def purge_expired(self) -> int:
        """Remove expired records. Returns the number removed."""
        return 0

    async def aclose(self) -> None:
        return None


def matches_metadata(record: MemoryRecord, metadata_filter: dict[str, Any] | None) -> bool:
    """Shared exact-match metadata filter.

    A list value in the filter means "any of these", which covers the common
    ``{"kind": ["note", "email"]}`` case without a query language.
    """
    if not metadata_filter:
        return True
    for field_name, expected in metadata_filter.items():
        actual = record.metadata.get(field_name)
        if isinstance(expected, (list, tuple, set)):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True
