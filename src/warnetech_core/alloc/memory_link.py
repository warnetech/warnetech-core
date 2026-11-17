"""Index allocated files into the warnetech memory store so ``get_context`` can find them.

Tier 0 and Tier 1 text files contribute their opening text. Everything else is indexed by
metadata only, which matches the plan (tier 2 is metadata-only, tier 3 is quarantine).
"""

from __future__ import annotations

from pathlib import Path

from warnetech_core.memory import warnetech_memory

from .classify import Classification
from .rules import Priority, Tier

_TEXT_PRIORITIES = frozenset({Priority.P0, Priority.P1})


def memory_text(item: Classification, head: str | None) -> str:
    header = (
        f"{Path(item.path).name} | {item.priority.value} | tier {int(item.tier)} | "
        + "; ".join(item.reasons)
    )
    if head and item.priority in _TEXT_PRIORITIES and item.tier is not Tier.QUARANTINE:
        return f"{header}\n{head.strip()}"
    return header


def index_item(memory_path: Path, item: Classification, head: str | None) -> str:
    """Store one item. Returns the memory status: "stored" or "duplicate"."""
    result = warnetech_memory.add(
        memory_path,
        memory_text(item, head),
        source=item.path,
        meta={
            "priority": item.priority.value,
            "tier": int(item.tier),
            "sha256": item.sha256,
            "size": item.size,
        },
    )
    return result.status
