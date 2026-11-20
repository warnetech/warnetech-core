"""Index allocated files into the warnetech memory store so ``get_context`` can find them.

Tier 0 and Tier 1 text files contribute their opening text. Everything else is indexed by
metadata only, which matches the plan (tier 2 is metadata-only, tier 3 is quarantine).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from warnetech_core.memory import warnetech_memory
from warnetech_core.memory.ingest import is_secret_name

from .classify import Classification
from .rules import Priority, Tier

_TEXT_PRIORITIES = frozenset({Priority.P0, Priority.P1})
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_KEY = r"[\w.-]{0,40}(?:password|passwd|secret|token|api[_-]?key|credential)[\w.-]{0,40}"
# key = value, "key": "value", export KEY=value: anywhere in a line, quoted or not.
_CREDENTIAL_PAIR = re.compile(
    rf"(?i)([\"']?{_KEY}[\"']?\s*[:=]\s*)(\"(?:[^\"\\\n]|\\.)*\"|'[^'\n]*'|[^\n]*)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
_TOKEN_SHAPES = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,})"
)
_REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    """Blank out credential-looking values before text is stored anywhere.

    Heuristic: it covers common shapes, not every secret. Over-redaction is the safe side.
    """
    text = _CREDENTIAL_PAIR.sub(lambda m: m.group(1) + _REDACTED, text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {_REDACTED}", text)
    text = _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}{_REDACTED}@", text)
    return _TOKEN_SHAPES.sub(_REDACTED, text)


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
        return f"{header}\n{body.strip()}"[: warnetech_memory.MAX_TEXT_CHARS]
    return header[: warnetech_memory.MAX_TEXT_CHARS]


def _meta(item: Classification) -> dict[str, object]:
    return {
        "priority": item.priority.value,
        "tier": int(item.tier),
        "sha256": item.sha256,
        "size": item.size,
    }


def index_item(memory_path: Path, item: Classification, head: str | None) -> str:
    """Store one item. Returns the memory status: "stored" or "duplicate"."""
    result = warnetech_memory.add(
        memory_path, memory_text(item, head), source=item.path, meta=_meta(item)
    )
    return result.status


def index_items(memory_path: Path, items: Sequence[tuple[Classification, str | None]]) -> int:
    """Store many items with one read and one write of the memory store. Returns how many
    were new."""
    results = warnetech_memory.add_many(
        memory_path,
        [(memory_text(item, head), item.path, _meta(item)) for item, head in items],
    )
    return sum(1 for r in results if r.status == "stored")


def resync_item(memory_path: Path, item: Classification, head: str | None) -> None:
    """Replace the memory record(s) for this file's content, so memory shows its current
    priority and tier."""
    for record in warnetech_memory.load(memory_path).records:
        if record.meta.get("sha256") == item.sha256:
            warnetech_memory.delete(memory_path, record.id)
    index_item(memory_path, item, head)
