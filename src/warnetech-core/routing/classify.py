"""Guess what kind of task a piece of text is, without calling a model.

A model call to decide which model to call would defeat the purpose on small
local hardware, so this is a short list of ordered rules. It is a heuristic: it
will misjudge unusual phrasing, which is why the router accepts an explicit kind.
Only the start of the text is examined, so the cost is bounded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KINDS = ("summarise", "extract", "code", "chat")
_SCAN_CHARS = 2000

_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("code", re.compile(r"```"), "contains a code block"),
    (
        "summarise",
        re.compile(
            r"\b(summari[sz]e|summary|tl;?dr|condense|shorten|key points|boil (?:this )?down)\b",
            re.IGNORECASE,
        ),
        "asks for a summary",
    ),
    (
        "extract",
        re.compile(
            r"\b(extract|json|csv|yaml|parse|list all|structured|fill in the fields"
            r"|convert (?:this )?to)\b",
            re.IGNORECASE,
        ),
        "asks for structured output",
    ),
    (
        "code",
        re.compile(
            r"\b(function|stack ?trace|traceback|refactor|compile|unit test|regex|sql query|bug)\b",
            re.IGNORECASE,
        ),
        "mentions programming",
    ),
)


@dataclass(frozen=True)
class Classification:
    kind: str
    reason: str


def classify(text: str) -> Classification:
    """Return the first matching rule's kind, or ``chat`` if none matches."""
    head = text[:_SCAN_CHARS]
    for kind, pattern, reason in _RULES:
        if pattern.search(head):
            return Classification(kind, reason)
    return Classification("chat", "no specific task detected")
