"""``warnetech agi`` command group: run a goal through the AGI loop.

``run`` plans the goal into tasks, reasons about each one, checks the answer with
a reflection pass, and retries within limits. It uses the configured models, or
the offline echo models when ``WARNETECH_OFFLINE=1``. Exit code 0 means every task
completed; 1 means the run stopped short.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from typing import Any

from .agi import AGIOrchestrator, Goal
from .cli_core import Command, CommandGroup, Param, report_config_errors
from .config import Settings

group = CommandGroup("agi", "run a goal through the plan, reason, act and reflect loop")


def _emit(text: str) -> None:
    sys.stdout.write(text + "\n")


@report_config_errors
def handle_run(values: Mapping[str, Any]) -> int:
    goal_text = str(values["goal"]).strip()
    max_cycles = int(values.get("max_cycles") or 20)
    gateway = values.get("_gateway") or Settings.from_env().build_gateway()

    async def go():
        try:
            orchestrator = AGIOrchestrator(gateway, max_cycles=max_cycles)
            return await orchestrator.run(Goal(description=goal_text))
        finally:
            await gateway.aclose()

    result = asyncio.run(go())
    for cycle in result.cycles:
        _emit(f"{cycle.cycle:>2}. [{cycle.outcome}] {cycle.task_title}")
    _emit(result.summary)
    return 0 if result.success else 1


group.register(
    Command(
        "run",
        "plan a goal into tasks and work through them",
        handle_run,
        (
            Param("goal", help="the objective, in plain language"),
            Param(
                "max_cycles",
                required=False,
                default=None,
                help="stop after this many task attempts (default 20)",
            ),
        ),
    )
)
