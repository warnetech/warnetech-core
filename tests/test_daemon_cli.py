"""Tests for daemon CLI and daemon lifecycle management."""

import asyncio
import os
from pathlib import Path

import pytest

from warnetech_core.runtime import (
    DaemonManager,
    ProcessConfig,
    ProcessStatus,
    ProcessSupervisor,
    get_daemon_logger,
    setup_daemon_logger,
)


class TestLogger:
    """Logger setup and functionality tests."""

    def test_setup_daemon_logger(self, tmp_path: Path) -> None:
        """Test daemon logger initialization."""
        log_file = tmp_path / "daemon.log"
        logger = setup_daemon_logger(log_file)

        assert logger is not None
        assert log_file.exists()
        logger.info("Test message")
        assert "Test message" in log_file.read_text()

    def test_get_daemon_logger(self, tmp_path: Path) -> None:
        """Test retrieving logger after setup."""
        log_file = tmp_path / "daemon.log"
        setup_daemon_logger(log_file)
        logger = get_daemon_logger()

        assert logger is not None
        logger.info("After get")
        assert "After get" in log_file.read_text()

    def test_daemon_logger_rotation(self, tmp_path: Path) -> None:
        """Test log rotation on file size limit."""
        log_file = tmp_path / "daemon.log"
        logger = setup_daemon_logger(log_file, max_bytes=100, backup_count=2)

        # Write logs larger than max_bytes
        for _ in range(5):
            logger.info("x" * 50)

        # Check that rotation happened
        assert log_file.exists()
        # Backup files created: daemon.log.1, daemon.log.2
        assert (tmp_path / "daemon.log.1").exists() or (tmp_path / "daemon.log.2").exists()


class TestDaemonManager:
    """DaemonManager lifecycle tests."""

    @pytest.mark.asyncio
    async def test_daemon_startup_shutdown(self, tmp_path: Path) -> None:
        """Test daemon startup and shutdown."""
        pid_file = tmp_path / "daemon.pid"
        log_dir = tmp_path / "logs"

        manager = DaemonManager(
            name="test-daemon",
            pid_file=pid_file,
            log_dir=log_dir,
        )

        async with manager:
            assert manager._running
            assert pid_file.exists()
            pid = int(pid_file.read_text().strip())
            assert pid == os.getpid()

        assert not manager._running
        assert not pid_file.exists()

    @pytest.mark.asyncio
    async def test_daemon_duplicate_startup(self, tmp_path: Path) -> None:
        """Test that starting already-running daemon raises error."""
        pid_file = tmp_path / "daemon.pid"
        log_dir = tmp_path / "logs"

        manager = DaemonManager(
            name="test-daemon",
            pid_file=pid_file,
            log_dir=log_dir,
        )

        await manager.startup()

        with pytest.raises(RuntimeError, match="already running"):
            await manager.startup()

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_daemon_stale_pid_cleanup(self, tmp_path: Path) -> None:
        """Test that stale PID files are cleaned up."""
        pid_file = tmp_path / "daemon.pid"
        log_dir = tmp_path / "logs"

        # Write stale PID (process that doesn't exist)
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("99999\n")

        manager = DaemonManager(
            name="test-daemon",
            pid_file=pid_file,
            log_dir=log_dir,
        )

        # Should succeed despite stale PID file
        await manager.startup()
        assert manager._running
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_daemon_add_task(self, tmp_path: Path) -> None:
        """Test adding tasks to daemon."""
        pid_file = tmp_path / "daemon.pid"
        log_dir = tmp_path / "logs"

        manager = DaemonManager(
            name="test-daemon",
            pid_file=pid_file,
            log_dir=log_dir,
        )

        async def dummy_task():
            await asyncio.sleep(0.01)
            return "done"

        await manager.startup()
        task = await manager.add_task(dummy_task(), name="dummy")
        result = await task
        assert result == "done"
        await manager.shutdown()


class TestProcessSupervisor:
    """ProcessSupervisor lifecycle tests."""

    @pytest.mark.asyncio
    async def test_process_start_stop(self) -> None:
        """Test process start and stop."""
        config = ProcessConfig(
            name="test-process",
            command=["sleep", "10"],
        )
        supervisor = ProcessSupervisor(config)

        await supervisor.start()
        assert supervisor.state.status == ProcessStatus.RUNNING
        assert supervisor.state.pid is not None

        await supervisor.stop(timeout=1.0)
        assert supervisor.state.status == ProcessStatus.STOPPED

    @pytest.mark.asyncio
    async def test_process_exit_tracking(self) -> None:
        """Test tracking process exit code."""
        config = ProcessConfig(
            name="test-exit",
            command=["python", "-c", "exit(42)"],
            auto_restart=False,
        )
        supervisor = ProcessSupervisor(config)

        await supervisor.start()
        # Give process time to exit
        await asyncio.sleep(0.5)
        # Monitor task should have detected exit
        assert supervisor.state.exit_code == 42

    @pytest.mark.asyncio
    async def test_process_restart(self) -> None:
        """Test process restart."""
        config = ProcessConfig(
            name="test-restart",
            command=["sleep", "10"],
            restart_delay=0.1,
        )
        supervisor = ProcessSupervisor(config)

        await supervisor.start()
        pid1 = supervisor.state.pid

        await supervisor.restart()
        # After restart, PID should be different
        assert supervisor.state.pid != pid1
        assert supervisor.state.status == ProcessStatus.RUNNING

        await supervisor.stop()

    @pytest.mark.asyncio
    async def test_process_auto_restart(self) -> None:
        """Test auto-restart on process exit."""
        config = ProcessConfig(
            name="test-auto-restart",
            command=["python", "-c", "exit(0)"],
            auto_restart=True,
            restart_delay=0.05,
        )
        supervisor = ProcessSupervisor(config)

        await supervisor.start()

        # Wait for process to exit and auto-restart
        await asyncio.sleep(0.5)

        # Process should be running again after auto-restart
        assert supervisor.state.status == ProcessStatus.RUNNING

        await supervisor.stop()

    @pytest.mark.asyncio
    async def test_process_config_dataclass(self) -> None:
        """Test ProcessConfig dataclass."""
        config = ProcessConfig(
            name="test",
            command=["echo", "hello"],
            auto_restart=False,
            restart_delay=2.0,
        )

        assert config.name == "test"
        assert config.command == ["echo", "hello"]
        assert config.auto_restart is False
        assert config.restart_delay == 2.0
        assert config.work_dir is None
        assert config.env is None

    @pytest.mark.asyncio
    async def test_process_status(self) -> None:
        """Test process status enumeration."""
        assert ProcessStatus.STOPPED.value == "stopped"
        assert ProcessStatus.RUNNING.value == "running"
        assert ProcessStatus.STOPPING.value == "stopping"
        assert ProcessStatus.RESTARTING.value == "restarting"
        assert ProcessStatus.ERROR.value == "error"
