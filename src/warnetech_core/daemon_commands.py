"""``warnetech daemon`` command group: manage the daemon lifecycle."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .cli_core import Command, CommandGroup, Param, report_config_errors
from .runtime.daemon import DaemonManager
from .runtime.logger import setup_daemon_logger

group = CommandGroup("daemon", "start, stop, status and logs for the warnetech daemon")


def _get_daemon_paths() -> tuple[Path, Path]:
    """Get daemon PID file and log directory paths."""
    pid_file = Path("/tmp/warnetech-daemon.pid")
    log_dir = Path("/tmp/warnetech-daemon-logs")
    return pid_file, log_dir


@report_config_errors
def handle_start(values: Mapping[str, Any]) -> int:
    """Start the daemon."""
    pid_file, log_dir = _get_daemon_paths()

    async def run():
        log_file = log_dir / "daemon.log"
        setup_daemon_logger(log_file)

        manager = DaemonManager(
            name="warnetech-daemon",
            pid_file=pid_file,
            log_dir=log_dir,
        )

        async with manager:
            sys.stdout.write(f"Daemon started (PID: {Path(pid_file).read_text().strip()})\n")
            # Keep daemon running until interrupted
            await manager.wait_until_stopped()

    try:
        asyncio.run(run())
        return 0
    except RuntimeError as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    except KeyboardInterrupt:
        sys.stdout.write("\nShutdown signal received\n")
        return 0


@report_config_errors
def handle_stop(values: Mapping[str, Any]) -> int:
    """Stop the daemon."""
    pid_file, log_dir = _get_daemon_paths()

    if not pid_file.exists():
        sys.stderr.write("Daemon not running (no PID file)\n")
        return 1

    try:
        pid = int(pid_file.read_text().strip())
        import os
        import signal

        os.kill(pid, signal.SIGTERM)
        sys.stdout.write(f"Sent SIGTERM to daemon (PID: {pid})\n")
        return 0
    except (ValueError, ProcessLookupError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


@report_config_errors
def handle_status(values: Mapping[str, Any]) -> int:
    """Check daemon status."""
    pid_file, log_dir = _get_daemon_paths()

    if not pid_file.exists():
        sys.stdout.write("not running\n")
        return 1

    try:
        pid = int(pid_file.read_text().strip())
        # Check if process exists
        import os

        os.kill(pid, 0)  # signal 0 is a no-op check
        sys.stdout.write(f"running (PID: {pid})\n")
        return 0
    except (ValueError, ProcessLookupError):
        sys.stdout.write("not running\n")
        return 1


@report_config_errors
def handle_logs(values: Mapping[str, Any]) -> int:
    """Show daemon logs."""
    pid_file, log_dir = _get_daemon_paths()
    log_file = log_dir / "daemon.log"

    if not log_file.exists():
        sys.stdout.write("(no logs)\n")
        return 0

    try:
        lines = int(values.get("lines", 50))
        with open(log_file) as f:
            all_lines = f.readlines()
            # Print last N lines
            for line in all_lines[-lines:]:
                sys.stdout.write(line)
        return 0
    except (OSError, ValueError) as e:
        sys.stderr.write(f"Error reading logs: {e}\n")
        return 1


# Register commands
group.register(Command("start", "Start the warnetech daemon", handle_start))
group.register(Command("stop", "Stop the warnetech daemon", handle_stop))
group.register(Command("status", "Check daemon status", handle_status))
group.register(
    Command(
        "logs",
        "Show daemon logs (default: last 50 lines)",
        handle_logs,
        (Param("lines", help="number of lines to show", required=False, default="50"),),
    )
)
