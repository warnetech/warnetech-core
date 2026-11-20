"""Process supervisor: manage subprocess lifecycle with async support."""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .logger import get_daemon_logger


class ProcessStatus(str, Enum):
    """Process status enumeration."""

    STOPPED = "stopped"
    RUNNING = "running"
    STOPPING = "stopping"
    RESTARTING = "restarting"
    ERROR = "error"


@dataclass
class ProcessConfig:
    """Configuration for a managed process."""

    name: str
    command: list[str]
    work_dir: Path | None = None
    env: dict[str, str] | None = None
    auto_restart: bool = True
    restart_delay: float = 1.0


@dataclass
class ProcessState:
    """Current state of a managed process."""

    status: ProcessStatus = ProcessStatus.STOPPED
    pid: int | None = None
    exit_code: int | None = None
    error: str | None = None


class ProcessSupervisor:
    """
    Manage subprocess lifecycle with async support.

    Handles start, stop, restart, status tracking, and auto-restart.
    """

    def __init__(
        self,
        config: ProcessConfig,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize process supervisor.

        Args:
            config: Process configuration
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger or get_daemon_logger()
        self.state = ProcessState()
        self._process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task | None = None

    async def start(self) -> None:
        """
        Start the managed process.

        Raises:
            RuntimeError: If process is already running
        """
        if self.state.status in (ProcessStatus.RUNNING, ProcessStatus.STOPPING):
            self.logger.warning("%s already running", self.config.name)
            return

        self.logger.info("Starting process: %s", self.config.name)
        self.state.status = ProcessStatus.RUNNING
        self.state.error = None

        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.config.command,
                cwd=self.config.work_dir,
                env=self.config.env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self.state.pid = self._process.pid
            self.logger.info("%s started (PID %d)", self.config.name, self._process.pid)

            # Start monitor task
            self._monitor_task = asyncio.create_task(self._monitor_process())
        except Exception as e:
            self.state.status = ProcessStatus.ERROR
            self.state.error = str(e)
            self.logger.error("Failed to start %s: %s", self.config.name, e)
            raise

    async def stop(self, timeout: float = 5.0) -> None:
        """
        Stop the managed process gracefully.

        Args:
            timeout: Seconds to wait before force-killing
        """
        if self.state.status == ProcessStatus.STOPPED:
            return

        if self.state.status == ProcessStatus.STOPPING:
            self.logger.warning("%s already stopping", self.config.name)
            return

        self.logger.info("Stopping process: %s", self.config.name)
        self.state.status = ProcessStatus.STOPPING

        if self._process is None or self._process.returncode is not None:
            self.state.status = ProcessStatus.STOPPED
            return

        try:
            # Try graceful termination
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                # Force kill if timeout
                self.logger.warning("Process %s not stopping, force killing", self.config.name)
                self._process.kill()
                await self._process.wait()

            self.state.exit_code = self._process.returncode
            self.logger.info(
                "%s stopped (exit code %d)",
                self.config.name,
                self._process.returncode,
            )
        except Exception as e:
            self.state.error = str(e)
            self.logger.error("Error stopping %s: %s", self.config.name, e)
        finally:
            self.state.status = ProcessStatus.STOPPED
            self._process = None
            if self._monitor_task:
                self._monitor_task.cancel()
                self._monitor_task = None

    async def restart(self) -> None:
        """Restart the process."""
        self.logger.info("Restarting process: %s", self.config.name)
        self.state.status = ProcessStatus.RESTARTING
        await self.stop()
        await asyncio.sleep(self.config.restart_delay)
        await self.start()

    def status(self) -> ProcessState:
        """Get current process state."""
        return self.state

    async def _monitor_process(self) -> None:
        """
        Monitor process for exit and handle auto-restart.

        Runs as background task.
        """
        if self._process is None:
            return

        try:
            await self._process.wait()
            exit_code = self._process.returncode
            self.logger.warning("%s exited with code %d", self.config.name, exit_code)

            if self.config.auto_restart and self.state.status == ProcessStatus.RUNNING:
                self.logger.info("Auto-restarting %s", self.config.name)
                await asyncio.sleep(self.config.restart_delay)
                await self.start()
            else:
                self.state.status = ProcessStatus.STOPPED
                self.state.exit_code = exit_code
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error("Monitor error for %s: %s", self.config.name, e)
            self.state.error = str(e)
            self.state.status = ProcessStatus.ERROR
