"""Integration tests for the daemon lifecycle (startup -> run -> shutdown).

These exercise the real DaemonManager against the real filesystem, asyncio
loop and OS process table. Only the filesystem is isolated, via tmp_path,
so tests run offline, in any order, and in parallel.
"""

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from warnetech_core.runtime.daemon import DaemonManager, _process_exists

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="signal-driven lifecycle is POSIX-only"
)


def _manager(tmp_path: Path, name: str = "warnetech-test") -> DaemonManager:
    return DaemonManager(
        name=name,
        pid_file=tmp_path / "run" / "daemon.pid",
        log_dir=tmp_path / "logs",
    )


def _spawn_daemon(
    pid_file: Path, log_dir: Path, ticks: int = 200, self_stop: bool = False
) -> subprocess.Popen:
    """Launch a real daemon in a child process that writes its PID and ticks."""
    script = textwrap.dedent(
        f"""
        import asyncio, sys
        from pathlib import Path
        from warnetech_core.runtime.daemon import DaemonManager

        async def main():
            d = DaemonManager("child", Path({str(pid_file)!r}), Path({str(log_dir)!r}))
            async with d:
                if {self_stop}:
                    await asyncio.sleep({ticks} * 0.05)
                    await d.shutdown()
                else:
                    await d.wait_until_stopped()
            print("clean-exit", flush=True)

        asyncio.run(main())
        """
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestStartupAndPidFile:
    @pytest.mark.asyncio
    async def test_startup_writes_current_pid(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        await mgr.startup()
        try:
            assert mgr.pid_file.read_text().strip() == str(os.getpid())
        finally:
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_startup_creates_directories(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        await mgr.startup()
        try:
            assert mgr.pid_file.parent.is_dir()
            assert mgr.log_dir.is_dir()
        finally:
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_second_startup_on_same_manager_raises(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        await mgr.startup()
        try:
            with pytest.raises(RuntimeError, match="already running"):
                await mgr.startup()
            assert mgr.pid_file.read_text().strip() == str(os.getpid())
        finally:
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_live_pid_blocks_second_daemon(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.pid_file.parent.mkdir(parents=True, exist_ok=True)
        mgr.pid_file.write_text(f"{os.getpid()}\n")  # this process is alive
        with pytest.raises(RuntimeError, match="already running"):
            await mgr.startup()
        # The live PID file must be left untouched.
        assert mgr.pid_file.read_text().strip() == str(os.getpid())

    @pytest.mark.asyncio
    async def test_stale_pid_is_replaced(self, tmp_path: Path) -> None:
        # Spawn and reap a child so its PID is guaranteed dead.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        mgr = _manager(tmp_path)
        mgr.pid_file.parent.mkdir(parents=True, exist_ok=True)
        mgr.pid_file.write_text(f"{dead.pid}\n")
        await mgr.startup()
        try:
            assert mgr.pid_file.read_text().strip() == str(os.getpid())
        finally:
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_corrupt_pid_file_is_removed_and_replaced(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.pid_file.parent.mkdir(parents=True, exist_ok=True)
        mgr.pid_file.write_text("not-a-pid")
        await mgr.startup()
        try:
            assert mgr.pid_file.read_text().strip() == str(os.getpid())
        finally:
            await mgr.shutdown()


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_removes_pid_file(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        await mgr.startup()
        await mgr.shutdown()
        assert not mgr.pid_file.exists()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_managed_tasks(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        await mgr.startup()

        async def forever() -> None:
            await asyncio.sleep(3600)

        task = await mgr.add_task(forever(), name="forever")
        await asyncio.sleep(0)  # let it start
        await mgr.shutdown()
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_shutdown_without_startup_is_noop(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        await mgr.shutdown()  # must not raise
        assert not mgr.pid_file.exists()

    @pytest.mark.asyncio
    async def test_context_manager_cleans_up_on_exception(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        with pytest.raises(ValueError):
            async with mgr:
                assert mgr.pid_file.exists()
                raise ValueError("boom")
        assert not mgr.pid_file.exists()


class TestTaskManagement:
    @pytest.mark.asyncio
    async def test_completed_tasks_are_pruned(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        await mgr.startup()
        try:

            async def quick() -> int:
                return 1

            task = await mgr.add_task(quick(), name="quick")
            await task
            await asyncio.sleep(0)  # let done-callback run
            assert task not in mgr._tasks
        finally:
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_wait_until_stopped_returns_after_shutdown(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        await mgr.startup()

        async def stopper() -> None:
            await asyncio.sleep(0.2)
            await mgr.shutdown()

        await asyncio.gather(mgr.wait_until_stopped(), stopper())
        assert not mgr.pid_file.exists()


class TestSignalDrivenLifecycle:
    """Real child process: SIGTERM must trigger a clean shutdown."""

    def test_sigterm_stops_child_and_removes_pid_file(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "run" / "child.pid"
        proc = _spawn_daemon(pid_file, tmp_path / "logs", ticks=2000)
        try:
            assert _wait_for(pid_file.exists), "child never wrote PID file"
            child_pid = int(pid_file.read_text().strip())
            assert child_pid == proc.pid
            assert _process_exists(child_pid)

            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)

            assert not pid_file.exists(), "PID file leaked after SIGTERM"
            assert not _process_exists(child_pid)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_natural_exit_removes_pid_file(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "run" / "child.pid"
        proc = _spawn_daemon(pid_file, tmp_path / "logs", ticks=5, self_stop=True)
        out, err = proc.communicate(timeout=15)
        assert proc.returncode == 0, err
        assert "clean-exit" in out
        assert not pid_file.exists()

    def test_second_child_refused_while_first_alive(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "run" / "child.pid"
        first = _spawn_daemon(pid_file, tmp_path / "logs", ticks=2000)
        try:
            assert _wait_for(pid_file.exists)
            second = _spawn_daemon(pid_file, tmp_path / "logs", ticks=5)
            _, err = second.communicate(timeout=15)
            assert second.returncode != 0
            assert "already running" in err
            # The first daemon's PID file must survive the refused start.
            assert int(pid_file.read_text().strip()) == first.pid
        finally:
            first.send_signal(signal.SIGTERM)
            first.wait(timeout=10)
