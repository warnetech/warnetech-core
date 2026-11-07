"""Validate, de-duplicate and split chat-format training data.

Each line of a ``.jsonl`` file is ``{"messages": [{"role": ..., "content": ...}]}``.
That is the shape most fine-tuning tools accept, so the output can feed them
directly.

Records that contain credential-shaped strings are rejected, not cleaned. A secret
that reaches training data is baked into the weights and cannot be taken back, so
the safe move is to stop and let a person fix the source.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ConfigError

VALID_ROLES = ("system", "user", "assistant")
MAX_MESSAGES = 64
MAX_CONTENT_CHARS = 20_000
MAX_RECORDS = 200_000
MAX_FILE_BYTES = 200 * 1024 * 1024

_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


@dataclass(frozen=True)
class Issue:
    line: int
    message: str


@dataclass
class LoadedDataset:
    records: list[dict[str, Any]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    duplicates: int = 0


def check_record(obj: Any) -> list[str]:
    """Return a list of problems with one record; empty means it is acceptable."""
    if not isinstance(obj, dict) or "messages" not in obj:
        return ['record must be an object with a "messages" list']
    messages = obj["messages"]
    if not isinstance(messages, list) or not 2 <= len(messages) <= MAX_MESSAGES:
        return [f"messages must be a list of 2 to {MAX_MESSAGES} items"]

    problems: list[str] = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") not in VALID_ROLES:
            problems.append(f"message {i}: role must be one of {VALID_ROLES}")
            continue
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            problems.append(f"message {i}: content must be a non-empty string")
            continue
        if len(content) > MAX_CONTENT_CHARS:
            problems.append(f"message {i}: content longer than {MAX_CONTENT_CHARS} characters")
        if _SECRET_RE.search(content):
            problems.append(f"message {i}: looks like it contains a credential; remove it")
    if problems:
        return problems

    roles = [m["role"] for m in messages]
    body = roles[1:] if roles[0] == "system" else roles
    if not body or body[0] != "user":
        problems.append("the first non-system message must be from the user")
    if roles[-1] != "assistant":
        problems.append("the last message must be from the assistant")
    for earlier, later in zip(body, body[1:], strict=False):  # pairs of neighbours
        if earlier == later:
            problems.append("user and assistant messages must alternate")
            break
    return problems


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_dataset(path: str | Path) -> LoadedDataset:
    """Read a JSONL file, collecting problems per line instead of stopping at one."""
    file = Path(path)
    try:
        size = file.stat().st_size
    except OSError as exc:
        raise ConfigError(f"cannot read {file}: {exc}") from exc
    if size > MAX_FILE_BYTES:
        raise ConfigError(f"{file} is larger than {MAX_FILE_BYTES} bytes")

    result = LoadedDataset()
    seen: set[str] = set()
    with file.open(encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            if len(result.records) >= MAX_RECORDS:
                result.issues.append(Issue(number, f"stopped after {MAX_RECORDS} records"))
                break
            try:
                obj = json.loads(line)
            except ValueError as exc:
                result.issues.append(Issue(number, f"not valid JSON: {exc}"))
                continue
            problems = check_record(obj)
            if problems:
                result.issues.extend(Issue(number, p) for p in problems)
                continue
            key = hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()
            if key in seen:
                result.duplicates += 1
                continue
            seen.add(key)
            result.records.append(obj)
    return result


def is_validation(record: dict[str, Any], *, val_fraction: float, seed: int) -> bool:
    """Decide which side of the split a record is on, from its content alone.

    Hashing the content (salted by ``seed``) rather than shuffling means a record
    stays on the same side when the file is reordered or grows, so a record that
    was held out for validation is never trained on later.
    """
    digest = hashlib.sha256(f"{seed}:{_canonical(record)}".encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") / 2**32
    return bucket < val_fraction


def split(
    records: Iterable[dict[str, Any]], *, val_fraction: float = 0.1, seed: int = 0
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(train, validation)``."""
    if not 0.0 <= val_fraction < 1.0:
        raise ConfigError("val_fraction must be at least 0 and less than 1")
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for record in records:
        target = val if is_validation(record, val_fraction=val_fraction, seed=seed) else train
        target.append(record)
    return train, val


def write_split(
    records: list[dict[str, Any]], out_dir: str | Path, *, val_fraction: float, seed: int
) -> tuple[int, int]:
    """Write ``train.jsonl`` and ``validation.jsonl``; return their sizes."""
    train, val = split(records, val_fraction=val_fraction, seed=seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("train.jsonl", train), ("validation.jsonl", val)):
        text = "".join(_canonical(r) + "\n" for r in rows)
        (out / name).write_text(text, encoding="utf-8")
    return len(train), len(val)
