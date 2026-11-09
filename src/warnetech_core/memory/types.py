"""Records and namespaces for the memory/context layer."""

from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# Namespaces appear in storage keys and SQL, so keep them boring.
_SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")

DEFAULT_SCOPE = "default"


def validate_name(value: str, *, kind: str) -> str:
    """Reject namespace components that are empty or contain separators."""
    if not _SAFE.match(value or ""):
        raise ValueError(
            f"invalid {kind} {value!r}: must be 1-128 chars of "
            f"[A-Za-z0-9._-] and start alphanumeric"
        )
    return value


@dataclass(frozen=True)
class Namespace:
    """A project's isolated slice of memory.

    Isolation is the default: a hub app writing to ``notes`` cannot see
    ``invoices`` unless a caller explicitly asks to search across both.

    Attributes:
        project: Owning project. The isolation boundary.
        scope: Optional sub-partition within a project — a user id, a session,
            a document collection.
    """

    project: str
    scope: str = DEFAULT_SCOPE

    def __post_init__(self) -> None:
        validate_name(self.project, kind="project")
        validate_name(self.scope, kind="scope")

    @property
    def key(self) -> str:
        """Flat storage key, ``project:scope``."""
        return f"{self.project}:{self.scope}"

    @classmethod
    def parse(cls, value: str | Namespace) -> Namespace:
        """Accept a ``Namespace``, ``"project"``, or ``"project:scope"``."""
        if isinstance(value, Namespace):
            return value
        project, _, scope = value.partition(":")
        return cls(project=project, scope=scope or DEFAULT_SCOPE)

    def __str__(self) -> str:
        return self.key


@dataclass
class MemoryRecord:
    """One stored piece of context.

    A record can be purely semantic (searched by embedding), purely keyed
    (fetched by ``key``, like a KV store), or both.
    """

    text: str
    namespace: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    # Registry alias that produced `embedding`. Vectors from different models
    # are not comparable even at equal dimensionality, so searches filter on it.
    embed_model: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    @property
    def project(self) -> str:
        return self.namespace.split(":", 1)[0]

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= self.expires_at

    def to_dict(self, *, include_embedding: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "namespace": self.namespace,
            "key": self.key,
            "text": self.text,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }
        data["embed_model"] = self.embed_model
        if include_embedding:
            data["embedding"] = self.embedding
        return data


@dataclass
class SearchHit:
    """A retrieved record and its similarity score (1.0 = identical direction)."""

    record: MemoryRecord
    score: float

    def to_dict(self, **kwargs: Any) -> dict[str, Any]:
        return {"score": round(self.score, 6), **self.record.to_dict(**kwargs)}


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity, tolerant of un-normalised inputs.

    Returns 0.0 for a zero vector rather than raising — an empty document
    should rank last, not crash a search.
    """
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} != {len(b)}")
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)
