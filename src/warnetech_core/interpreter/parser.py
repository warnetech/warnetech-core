"""Pull runnable code out of a model reply.

Model output is untrusted. This module only extracts text; it never runs it.
Only the first fenced block counts, so a reply cannot smuggle a second action
past the approver, and an unlabelled block is refused rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"```[ \t]*([\w+-]*)[ \t]*\n(.*?)```", re.DOTALL)
_ALIASES = {"py": "python", "python3": "python", "sh": "bash", "shell": "bash", "zsh": "bash"}


@dataclass(frozen=True)
class CodeBlock:
    language: str  # "" when the fence had no label
    code: str


def first_block(text: str) -> CodeBlock | None:
    """Return the first fenced code block in ``text``, or ``None``."""
    match = _FENCE_RE.search(text)
    if match is None:
        return None
    label = match.group(1).lower()
    return CodeBlock(language=_ALIASES.get(label, label), code=match.group(2).rstrip("\n"))
