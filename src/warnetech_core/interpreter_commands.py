"""``warnetech interpreter`` command group (natural-language computer control).

``run <task>`` takes four parameters at most (task, model, workspace, mode). The
CLI offers ``ask`` and ``dry-run`` only; ``auto`` cannot be selected from the
command line.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping
from typing import Any

from .cli_core import Command, CommandGroup, Param
from .config import Settings
from .interpreter import (
    AuditLog,
    Executor,
    Interpreter,
    Policy,
    Workspace,
    prompt_approver,
)

group = CommandGroup("interpreter", "control the computer in natural language, with local models")

DEFAULT_WORKSPACE = "./workspace"
DEFAULT_AUDIT = "./data/interpreter_audit.jsonl"


def _emit(text: str) -> None:
    sys.stdout.write(text + "\n")


def handle_run(values: Mapping[str, Any]) -> int:
    gateway = values.get("_gateway") or Settings.from_env().build_gateway()
    approver = values.get("_approver") or prompt_approver
    workspace = Workspace(values.get("workspace") or DEFAULT_WORKSPACE)
    audit = AuditLog(os.getenv("WARNETECH_INTERPRETER_AUDIT") or DEFAULT_AUDIT)
    executor = Executor(workspace)
    if not executor.memory_limit_enforced:
        sys.stderr.write(
            "warning: the memory limit is not enforced on this platform; approve code "
            "only if you trust it not to use a lot of memory\n"
        )
    interpreter = Interpreter(
        gateway,
        executor,
        model=values.get("model") or "chat.local",
        policy=Policy(mode=values.get("mode") or "ask"),
        approver=approver,
        audit=audit,
    )
    report = asyncio.run(interpreter.run(str(values["task"])))

    for i, step in enumerate(report.steps, 1):
        _emit(f"step {i} [{step.action.language}] {'ran' if step.approved else step.note}")
        if step.result is not None:
            _emit(step.result.stdout.rstrip() or "(no output)")
            if step.result.stderr.strip():
                _emit(f"stderr: {step.result.stderr.rstrip()}")
    if report.final:
        _emit(report.final)
    _emit(f"[{report.stopped}]")
    return 0 if report.stopped in {"done", "dry-run"} else 1


group.register(
    Command(
        "run",
        "give the interpreter a task in plain language",
        handle_run,
        (
            Param("task", help="what you want done, in plain language"),
            Param("model", required=False, default="chat.local", help="registry alias to use"),
            Param(
                "workspace",
                required=False,
                default=DEFAULT_WORKSPACE,
                help="the only directory it may read or write",
            ),
            Param(
                "mode",
                required=False,
                default="ask",
                choices=("ask", "dry-run"),
                help="ask: approve each action; dry-run: show proposals, run nothing",
            ),
        ),
    )
)
