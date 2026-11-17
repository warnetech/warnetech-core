"""Priority rules: built-in defaults plus an optional YAML override file."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class Priority(str, Enum):
    P0 = "P0"  # critical: pinned, never evicted
    P1 = "P1"  # active project and operational
    P2 = "P2"  # reference and historical
    P3 = "P3"  # transient, duplicate, unknown: quarantine


class Tier(int, Enum):
    HOT = 0
    WARM = 1
    COLD = 2
    QUARANTINE = 3


_TIER_FOR = {
    Priority.P0: Tier.HOT,
    Priority.P1: Tier.WARM,
    Priority.P2: Tier.WARM,  # eligible for tier 2 later; stays warm for now
    Priority.P3: Tier.QUARANTINE,
}


def tier_for(priority: Priority) -> Tier:
    """The tier a priority is allocated to."""
    return _TIER_FOR[priority]


@dataclass(frozen=True)
class Rules:
    active_days: float = 14.0
    reference_days: float = 30.0
    critical_name_patterns: tuple[str, ...] = ("warnetech-", "warnetech_", "critical")
    critical_content_markers: tuple[str, ...] = ("[CRITICAL]",)
    transient_suffixes: tuple[str, ...] = (".tmp", ".part", ".crdownload", ".download", "~")
    operational_suffixes: frozenset[str] = frozenset(
        {".md", ".txt", ".log", ".py", ".sh", ".yaml", ".yml", ".json", ".csv", ".toml"}
    )
    reference_suffixes: frozenset[str] = frozenset({".pdf", ".epub", ".docx"})
    sniff_bytes: int = 4096
    tier_quota_bytes: dict[int, int] = field(default_factory=dict)


_KEYS = {
    "active_days",
    "reference_days",
    "critical_name_patterns",
    "critical_content_markers",
    "transient_suffixes",
    "operational_suffixes",
    "reference_suffixes",
    "sniff_bytes",
    "tier_quota_bytes",
}


def _str_tuple(name: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name} must be a list of strings")
    return tuple(value)


def load_rules(path: Path | None) -> Rules:
    """Return the defaults, or the defaults overridden by ``path``. Unknown keys are errors."""
    if path is None:
        return Rules()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    unknown = set(raw) - _KEYS
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")

    base = Rules()
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key in ("active_days", "reference_days"):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{key} must be a positive number")
            values[key] = float(value)
        elif key == "sniff_bytes":
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("sniff_bytes must be a positive integer")
            values[key] = value
        elif key in ("operational_suffixes", "reference_suffixes"):
            values[key] = frozenset(_str_tuple(key, value))
        elif key == "tier_quota_bytes":
            if not isinstance(value, dict):
                raise ValueError("tier_quota_bytes must map tier number to bytes")
            values[key] = {int(Tier(int(t))): int(b) for t, b in value.items()}
        else:
            values[key] = _str_tuple(key, value)

    merged = {**base.__dict__, **values}
    rules = Rules(**merged)
    if rules.reference_days <= rules.active_days:
        raise ValueError("reference_days must be greater than active_days")
    return rules
