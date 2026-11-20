"""``warnetech init`` command: initialize warnetech environment."""

from __future__ import annotations

import sys
from pathlib import Path


def handle_init(non_interactive: bool = False) -> int:
    """Initialize warnetech environment.

    Args:
        non_interactive: If True, skip prompts and use defaults.

    Returns:
        0 for success, 1 for failure.
    """
    try:
        # Create necessary directories for daemon
        pid_file = Path("/tmp/warnetech-daemon.pid")
        log_dir = Path("/tmp/warnetech-daemon-logs")

        pid_file.parent.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Initialize message
        if non_interactive:
            sys.stdout.write("Initialized warnetech (non-interactive mode)\n")
        else:
            sys.stdout.write("Initialized warnetech\n")

        return 0
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
