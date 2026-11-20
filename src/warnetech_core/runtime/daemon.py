"""Async daemon lifecycle management with signal handling and PID lockfile."""

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable
from pathlib import Path

from .logger import get_daemon_logger


class DaemonManager:
    """
    Manages daemon lifecycle: startup, shutdown, signal handling, PID lockfile.

    Provides async context manager interface for daemon operations.
    """

    def __init__(
        self,
        name: str,
        pid_file: Path,
        log_dir: Path,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize daemon manager.

        Args:
            name: Daemon name (e.g., "warnetech-supervisor")
            pid_file: Path to PID lockfile
            log_dir: Directory for logs
            logger: Optional logger (uses daemon logger if None)
        """
        self.name = name
        self.pid_file = Path(pid_file)
        self.log_dir = Path(log_dir)
        self.logger = logger or get_daemon_logger()
        self._running = False
        self._tasks: set[asyncio.Task] = set()

    async def __aenter__(self):
        """Start daemon on context entry."""
        await self.startup()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Stop daemon on context exit."""
        await self.shutdown()

    async def startup(self) -> None:
        """
        Start daemon: write PID, set up signal handlers.

        Raises:
            RuntimeError: If daemon is already running (PID file exists and process alive)
        """
        if self._running:
            self.logger.warning("%s already running", self.name)
            return

        # Check for existing process
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip())
                os.kill(pid, 0)  # Check if process exists
                raise RuntimeError(f"{self.name} already running with PID {pid}")
            except (ProcessLookupError, ValueError):
                # PID file stale, remove it
                self.pid_file.unlink(missing_ok=True)

        # Ensure directories exist
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Write PID file
        pid = os.getpid()
        self.pid_file.write_text(f"{pid}\n")
        self.logger.info("%s started (PID %d)", self.name, pid)

        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_handler, sig)

        self._running = True

    async def shutdown(self) -> None:
        """Stop daemon: cancel tasks, clean up PID file."""
        if not self._running:
            return

        self.logger.info("%s shutting down", self.name)

        # Cancel pending tasks
        pending = asyncio.all_tasks()
        for task in pending:
            if task is not asyncio.current_task():
                task.cancel()

        # Wait for cancellation
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        # Remove PID file
        self.pid_file.unlink(missing_ok=True)
        self.logger.info("%s stopped", self.name)
        self._running = False

    def _signal_handler(self, sig: signal.Signals) -> None:
        """Handle shutdown signals."""
        self.logger.info("Received signal %s, initiating shutdown", sig.name)
        asyncio.create_task(self.shutdown())

    async def add_task(self, coro: Awaitable, name: str | None = None) -> asyncio.Task:
        """
        Add a managed task to the daemon.

        Args:
            coro: Coroutine to run
            name: Optional task name

        Returns:
            Task handle
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_until_stopped(self) -> None:
        """Block until daemon is stopped (useful for daemon process)."""
        while self._running:
            await asyncio.sleep(0.1)
