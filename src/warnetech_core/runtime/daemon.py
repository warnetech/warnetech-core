"""Async daemon lifecycle management with signal handling and PID lockfile."""

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Awaitable
from pathlib import Path

from .logger import get_daemon_logger


def _process_exists(pid: int) -> bool:
    """
    Check if a process with the given PID exists (cross-platform safe).

    On Windows, avoid os.kill(pid, 0) which sends CTRL_C_EVENT.
    On POSIX, PermissionError means the process exists but we can't probe it.
    """
    if sys.platform == "win32":
        # On Windows, try to open the process handle
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(
                0, False, pid
            )  # PROCESS_QUERY_LIMITED_INFORMATION = 0
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        # On POSIX, signal 0 is a safe no-op probe
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we don't have permission to check it
            return True


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
            raise RuntimeError(f"{self.name} already running in this process")

        # Check for existing process
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip())
                if _process_exists(pid):
                    raise RuntimeError(f"{self.name} already running with PID {pid}")
            except ValueError:
                # PID file contains invalid data, remove it
                self.pid_file.unlink(missing_ok=True)
            else:
                # PID file stale, remove it
                self.pid_file.unlink(missing_ok=True)

        # Ensure directories exist
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Write PID file
        pid = os.getpid()
        self.pid_file.write_text(f"{pid}\n")
        self.logger.info("%s started (PID %d)", self.name, pid)

        # Set up signal handlers (Unix/Linux only; Windows doesn't support this)
        loop = asyncio.get_event_loop()
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self._signal_handler, sig)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler; skip gracefully
            self.logger.debug("Signal handlers not supported on this platform")

        self._running = True

    async def shutdown(self) -> None:
        """Stop daemon: cancel tasks, clean up PID file."""
        if not self._running:
            return

        self.logger.info("%s shutting down", self.name)

        # Remove signal handlers installed in startup() so they don't leak
        # into the host process (e.g. a test runner).
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError):
                pass

        # Cancel only tasks this daemon manages, never the host's tasks.
        pending = [t for t in self._tasks if not t.done()]
        for task in pending:
            task.cancel()
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
