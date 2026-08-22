"""The memory & context layer.

One shared implementation of "does this AI remember X", so each mini-project
gets namespaced storage, embedding and retrieval without building its own.

Isolation is the default and crossing it is explicit: ``recall("notes", ...)``
can never surface a record written by the ``invoices`` project unless the caller
names it in ``also_projects``.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..gateway import AIGateway
from .base import ContextStore
from .inmemory import InMemoryContextStore
from .types import DEFAULT_SCOPE, MemoryRecord, Namespace, SearchHit

# ~4 characters per token. Deliberately crude: this only has to keep an
# assembled context block roughly inside a budget, not bill anyone.
CHARS_PER_TOKEN = 4


@dataclass
class ContextResult:
    """What :meth:`MemoryService.get_context` hands back.

    Attributes:
        text: Retrieved records formatted as one block, ready to drop into a
            prompt. Empty when nothing matched.
        hits: The records behind ``text``, best-scoring first, so a caller can
            cite sources or apply its own re-ranking.
        truncated: Whether some qualifying hits were dropped for the budget.
    """

    query: str
    text: str
    hits: list[SearchHit] = field(default_factory=list)
    truncated: bool = False
    namespaces: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.hits

    @property
    def estimated_tokens(self) -> int:
        return len(self.text) // CHARS_PER_TOKEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "text": self.text,
            "truncated": self.truncated,
            "namespaces": self.namespaces,
            "estimated_tokens": self.estimated_tokens,
            "hits": [h.to_dict() for h in self.hits],
        }


class MemoryService:
    """Namespaced, embedding-backed memory over a pluggable store."""

    def __init__(
        self,
        gateway: AIGateway,
        store: ContextStore | None = None,
        *,
        embed_model: str = "embed.default",
        default_scope: str = DEFAULT_SCOPE,
    ) -> None:
        self.gateway = gateway
        self.store = store or InMemoryContextStore()
        self.embed_model = embed_model
        self.default_scope = default_scope

    # ---- namespacing ----------------------------------------------------

    def namespace(self, project: str, scope: str | None = None) -> Namespace:
        return Namespace(project=project, scope=scope or self.default_scope)

    def _search_namespaces(
        self,
        project: str,
        *,
        scope: str | None,
        scopes: Sequence[str] | None,
        also_projects: Sequence[str] | None,
    ) -> list[str]:
        """Build the namespace allowlist for a read.

        Everything outside the calling project must be named explicitly — that
        is the whole isolation guarantee.
        """
        wanted_scopes = list(scopes) if scopes else [scope or self.default_scope]
        namespaces = [Namespace(project=project, scope=s).key for s in wanted_scopes]
        for other in also_projects or ():
            namespaces.extend(Namespace(project=other, scope=s).key for s in wanted_scopes)
        # De-duplicate, preserving caller order.
        return list(dict.fromkeys(namespaces))

    # ---- embedding ------------------------------------------------------

    async def _embed(self, texts: Sequence[str], *, project: str) -> list[list[float]]:
        response = await self.gateway.embed(self.embed_model, list(texts), project=project)
        return response.vectors

    # ---- writes ---------------------------------------------------------

    async def remember(
        self,
        project: str,
        text: str,
        *,
        key: str | None = None,
        scope: str | None = None,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
        embed: bool = True,
    ) -> MemoryRecord:
        """Store one piece of context.

        Args:
            key: Stable identifier. Writing the same key again updates in place
                instead of accumulating duplicates.
            ttl_seconds: Expire the record after this long. Useful for session
                state you do not want to keep forever.
            embed: Set false for records you will only ever fetch by ``key`` —
                it skips the embedding call and its cost.
        """
        records = await self.remember_many(
            project,
            [{"text": text, "key": key, "metadata": metadata, "ttl_seconds": ttl_seconds}],
            scope=scope,
            embed=embed,
        )
        return records[0]

    async def remember_many(
        self,
        project: str,
        items: Sequence[dict[str, Any] | str],
        *,
        scope: str | None = None,
        embed: bool = True,
    ) -> list[MemoryRecord]:
        """Store a batch. Embeddings are fetched in one call, not N."""
        namespace = self.namespace(project, scope).key
        now = time.time()

        normalised: list[dict[str, Any]] = [
            {"text": item} if isinstance(item, str) else dict(item) for item in items
        ]
        if not normalised:
            return []
        for item in normalised:
            if not item.get("text"):
                raise ValueError("every memory item needs non-empty `text`")

        vectors: list[list[float] | None] = [None] * len(normalised)
        if embed:
            embedded = await self._embed([i["text"] for i in normalised], project=project)
            vectors = list(embedded)

        records = []
        for item, vector in zip(normalised, vectors, strict=True):
            ttl = item.get("ttl_seconds")
            records.append(
                MemoryRecord(
                    text=item["text"],
                    namespace=namespace,
                    key=item.get("key"),
                    metadata=item.get("metadata") or {},
                    embedding=vector,
                    expires_at=now + ttl if ttl else None,
                )
            )
        return await self.store.upsert(records)

    async def forget(
        self,
        project: str,
        *,
        key: str | None = None,
        id: str | None = None,
        scope: str | None = None,
    ) -> int:
        """Delete by key or id. Returns how many records went."""
        return await self.store.delete(
            self.namespace(project, scope).key, id=id, key=key
        )

    async def clear(self, project: str, *, scope: str | None = None) -> int:
        """Drop every record in one project scope."""
        return await self.store.clear(self.namespace(project, scope).key)

    # ---- reads ----------------------------------------------------------

    async def get(
        self,
        project: str,
        *,
        key: str | None = None,
        id: str | None = None,
        scope: str | None = None,
    ) -> MemoryRecord | None:
        """Exact fetch by key or id — no embedding call involved."""
        return await self.store.get(self.namespace(project, scope).key, id=id, key=key)

    async def recall(
        self,
        project: str,
        query: str,
        *,
        limit: int = 5,
        scope: str | None = None,
        scopes: Sequence[str] | None = None,
        also_projects: Sequence[str] | None = None,
        min_score: float = 0.0,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Semantic search within ``project`` (and only there, by default)."""
        namespaces = self._search_namespaces(
            project, scope=scope, scopes=scopes, also_projects=also_projects
        )
        vector = (await self._embed([query], project=project))[0]
        return await self.store.search(
            namespaces,
            vector,
            limit=limit,
            min_score=min_score,
            metadata_filter=metadata_filter,
        )

    async def get_context(
        self,
        project: str,
        query: str,
        *,
        limit: int = 5,
        max_tokens: int = 2000,
        scope: str | None = None,
        scopes: Sequence[str] | None = None,
        also_projects: Sequence[str] | None = None,
        min_score: float = 0.0,
        metadata_filter: dict[str, Any] | None = None,
        include_scores: bool = False,
    ) -> ContextResult:
        """Retrieve and assemble context for a prompt.

        This is the simple retrieval API the hub apps call:
        ``await memory.get_context("hub-notes", "what did we decide about X?")``

        Hits are packed best-first until ``max_tokens`` is reached. A record too
        large for the remaining budget is skipped rather than cut mid-sentence,
        and a smaller one behind it may still fit.
        """
        namespaces = self._search_namespaces(
            project, scope=scope, scopes=scopes, also_projects=also_projects
        )
        hits = await self.recall(
            project,
            query,
            limit=limit,
            scope=scope,
            scopes=scopes,
            also_projects=also_projects,
            min_score=min_score,
            metadata_filter=metadata_filter,
        )

        budget = max_tokens * CHARS_PER_TOKEN
        used = 0
        kept: list[SearchHit] = []
        blocks: list[str] = []
        truncated = False

        for index, hit in enumerate(hits, start=1):
            block = self._format_hit(index, hit, include_scores=include_scores)
            if used + len(block) > budget:
                truncated = True
                continue
            used += len(block)
            kept.append(hit)
            blocks.append(block)

        return ContextResult(
            query=query,
            text="\n\n".join(blocks),
            hits=kept,
            truncated=truncated,
            namespaces=namespaces,
        )

    @staticmethod
    def _format_hit(index: int, hit: SearchHit, *, include_scores: bool) -> str:
        """Render one hit as a labelled block a model can cite."""
        parts = [f"source={hit.record.key or hit.record.id}"]
        if include_scores:
            parts.append(f"score={hit.score:.3f}")
        for field_name in ("title", "kind", "url", "date"):
            value = hit.record.metadata.get(field_name)
            if value is not None:
                parts.append(f"{field_name}={value}")
        return f"[{index}] ({' '.join(parts)})\n{hit.record.text}"

    async def list(
        self,
        project: str,
        *,
        scope: str | None = None,
        limit: int = 100,
        offset: int = 0,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        return await self.store.list(
            self.namespace(project, scope).key,
            limit=limit,
            offset=offset,
            metadata_filter=metadata_filter,
        )

    async def count(self, project: str, *, scope: str | None = None) -> int:
        return await self.store.count(self.namespace(project, scope).key)

    async def projects(self) -> list[str]:
        """Every project that has written at least one record."""
        return sorted({ns.split(":", 1)[0] for ns in await self.store.namespaces()})

    async def purge_expired(self) -> int:
        return await self.store.purge_expired()

    async def aclose(self) -> None:
        await self.store.aclose()
