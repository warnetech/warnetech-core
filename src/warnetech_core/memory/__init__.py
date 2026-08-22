"""Memory & context layer: namespaced storage, embedding and retrieval."""

from .base import ContextStore
from .inmemory import InMemoryContextStore
from .service import ContextResult, MemoryService
from .sqlite import SQLiteContextStore
from .types import MemoryRecord, Namespace, SearchHit

__all__ = [
    "ContextResult",
    "ContextStore",
    "InMemoryContextStore",
    "MemoryRecord",
    "MemoryService",
    "Namespace",
    "PgVectorContextStore",
    "SQLiteContextStore",
    "SearchHit",
    "SupabaseVectorStore",
]


def __getattr__(name: str):  # pragma: no cover - optional dependency shim
    if name == "PgVectorContextStore":
        from .pgvector import PgVectorContextStore

        return PgVectorContextStore
    if name == "SupabaseVectorStore":
        from .supabase import SupabaseVectorStore

        return SupabaseVectorStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
