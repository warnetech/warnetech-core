"""What the interpreter is allowed to do, and who must approve it.

Modes
-----
``ask``      every action is shown to an approver before it runs (the default)
``dry-run``  the model's proposed action is recorded and never executed
``auto``     actions run without approval. There is deliberately no CLI or MCP
             path to this mode: it exists only for code that builds a ``Policy``
             itself, in a sandboxed environment it controls.

The default approver denies. A missing or non-interactive approver therefore
fails closed instead of running unreviewed code.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

Mode = Literal["ask", "dry-run", "auto"]
_MODES = ("ask", "dry-run", "auto")


@dataclass(frozen=True)
class Action:
    """One thing the model wants to run."""

    language: str
    code: str
    workspace: str


Approver = Callable[[Action], bool]


@dataclass(frozen=True)
class Policy:
    mode: Mode = "ask"
    max_steps: int = 6

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise ValueError(f"unknown mode {self.mode!r}; expected one of {_MODES}")
        if not 1 <= self.max_steps <= 20:
            raise ValueError("max_steps must be between 1 and 20")


def deny_all(_action: Action) -> bool:
    """The safe default approver."""
    return False


def prompt_approver(action: Action) -> bool:
    """Show the code on stderr and ask. Denies without a terminal."""
    if not sys.stdin.isatty():
        return False
    sys.stderr.write(
        f"\nThe model wants to run {action.language} in {action.workspace}:\n"
        f"{'-' * 60}\n{action.code}\n{'-' * 60}\nRun this? [y/N] "
    )
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in {"y", "yes"}
