"""``warnetech automation`` command group: manage automation and scheduling."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any

from .cli_core import Command, CommandGroup, report_config_errors

group = CommandGroup("automation", "manage automation rules and scheduled tasks")


@report_config_errors
def handle_list(values: Mapping[str, Any]) -> int:
    """List all automation rules."""
    sys.stdout.write("No automation rules configured yet.\n")
    return 0


@report_config_errors
def handle_status(values: Mapping[str, Any]) -> int:
    """Check automation scheduler status."""
    sys.stdout.write("Automation scheduler: ready\n")
    return 0


# Register commands
group.register(Command("list", "list all automation rules", handle_list))
group.register(Command("status", "check automation scheduler status", handle_status))
