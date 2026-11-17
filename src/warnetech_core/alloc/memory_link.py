"""Index allocated files into the warnetech memory store so ``get_context`` can find them.

Tier 0 and Tier 1 text files contribute their opening text. Everything else is indexed by
metadata only, which matches the plan (tier 2 is metadata-only, tier 3 is quarantine).
"""

from __future__ import annotations

import re
from pathlib import Path

from warnetech_core.memory import warnetech_memory
from warnetech_core.memory.ingest import is_secret_name

from .classify import Classification
from .rules import Priority, Tier

_TEXT_PRIORITIES = frozenset({Priority.P0, Priority.P1})
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_CREDENTIAL_LINE = re.compile(
    r"(?im)^(\s*[\w.-]*(?:password|passwd|secret|token|api[_-]?key|credential)[\w.-]*\s*[:=]\s*)\S.*$"
)
_REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    """Blank out credential-looking values before text is stored anywhere."""
    return _CREDENTIAL_LINE.sub(lambda m: m.group(1) + _REDACTED, text)


def safe_head(path: Path, head: str | None) -> str | None:
    """Return the text fit for the memory store, or None if it must stay header-only."""
    if head is None or is_secret_name(path.name) or _PRIVATE_KEY.search(head):
        return None
    return redact(head)


def memory_text(item: Classification, head: str | None) -> str:
    header = (
        f"{Path(item.path).name} | {item.priority.value} | tier {int(item.tier)} | "
        + "; ".join(item.reasons)
    )
    body = safe_head(Path(item.path), head)
    if body and item.priority in _TEXT_PRIORITIES and item.tier is not Tier.QUARANTINE:
        return f"{header}\n{body.strip()}"
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
