"""Two-pass classifier: filename and metadata first, then a bounded content sniff."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .rules import Priority, Rules, Tier

_SECONDS_PER_DAY = 86_400
_HASH_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Classification:
    path: str
    size: int
    sha256: str
    priority: Priority
    tier: Tier
    reasons: tuple[str, ...]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sniff(path: Path, rules: Rules) -> tuple[bool, str]:
    """Return (is_binary, decoded_head). Reads at most ``rules.sniff_bytes`` bytes."""
    with path.open("rb") as handle:
        head = handle.read(rules.sniff_bytes)
    is_binary = b"\x00" in head
    return is_binary, head.decode("utf-8", errors="replace")


def text_head(path: Path, rules: Rules) -> str | None:
    """The opening text of a text file, or None for binary content."""
    is_binary, head = _sniff(path, rules)
    return None if is_binary else head


def classify(
    path: Path,
    rules: Rules,
    now: float,
    *,
    sha256: str,
    is_duplicate: bool = False,
) -> Classification:
    stat = path.stat()
    name = path.name.lower()
    suffix = path.suffix.lower()
    age_days = (now - stat.st_mtime) / _SECONDS_PER_DAY
    reasons: list[str] = []

    def done(priority: Priority, tier: Tier) -> Classification:
        return Classification(str(path), stat.st_size, sha256, priority, tier, tuple(reasons))

    # Pass 1: filename and metadata.
    if any(pattern in name for pattern in rules.critical_name_patterns):
        reasons.append("filename matches a critical pattern")
        return done(Priority.P0, Tier.HOT)
    if name.endswith(rules.transient_suffixes):
        reasons.append("transient file (temporary or partial download)")
        return done(Priority.P3, Tier.QUARANTINE)
    if is_duplicate:
        reasons.append("duplicate content (same SHA-256 already seen)")
        return done(Priority.P3, Tier.QUARANTINE)

    # Pass 2: bounded content sniff. Only needed when the name did not decide.
    is_binary, head = _sniff(path, rules)
    if is_binary and suffix not in rules.reference_suffixes:
        reasons.append("binary content with no known reader")
        return done(Priority.P3, Tier.QUARANTINE)
    if not is_binary and any(marker in head for marker in rules.critical_content_markers):
        reasons.append("content carries a critical marker")
        return done(Priority.P0, Tier.HOT)

    # Recency decides the rest.
    if age_days <= rules.active_days:
        reasons.append(f"modified {age_days:.1f} days ago (active)")
        return done(Priority.P1, Tier.WARM)
    if age_days <= rules.reference_days:
        reasons.append(
            f"modified {age_days:.1f} days ago (between active and reference thresholds; "
            "defaulted to P1)"
        )
        return done(Priority.P1, Tier.WARM)
    if suffix in rules.operational_suffixes or suffix in rules.reference_suffixes:
        reasons.append(f"inactive {age_days:.1f} days (reference or historical)")
        return done(Priority.P2, Tier.WARM)
    reasons.append(f"inactive {age_days:.1f} days and unknown file type")
    return done(Priority.P3, Tier.QUARANTINE)
