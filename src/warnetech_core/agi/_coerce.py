"""Helpers for turning untrusted model output into safe values.

Model replies are data, not a contract: numbers can be out of range, fields can
be null or the wrong type, and JSON can be wrapped in a Markdown fence. These
helpers normalise such input so the AGI parsers never raise on it.
"""

from __future__ import annotations

import math
from typing import Any


def unit_interval(value: Any, default: float = 0.5) -> float:
    """Return *value* as a float clamped to [0, 1], or *default* if it is not a number."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number):
        return default
    return min(1.0, max(0.0, number))


def strip_code_fence(text: str) -> str:
    """Remove one surrounding ```` ``` ```` fence (with or without a language tag)."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines[-1].strip() == "```":
        return "\n".join(lines[1:-1])
    return "\n".join(lines[1:])
