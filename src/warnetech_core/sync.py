"""Sync access to the async gateway.

Most of Warnetech is synchronous — ``warnetech_ai_controller.embeddings``
takes a plain ``Callable[[str], list[float]]``, and the CLI and control-plane
engines are ordinary functions. The gateway is async because it does network
I/O with retries. This module bridges the two.

The naive bridge (``asyncio.run`` per call) is wrong here in two ways: it
raises if the caller already has a running loop, and it builds a fresh event
loop each time, which breaks ``httpx.AsyncClient`` — the client's connection
pool is bound to the loop it was created on. So :class:`SyncRunner` owns one
long-lived loop on a daemon thread and submits every coroutine to it. Provider
clients then live on a single stable loop for the runner's lifetime, and
calling from inside an async context works too, because the work happens on a
different thread.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class SyncRunner:
    """Runs coroutines from synchronous code on one dedicated event loop.

    Thread-safe. The loop thread is a daemon, so it never blocks interpreter
    exit; call :meth:`close` for a deterministic shutdown.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None:
                return self._loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run() -> None:
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            thread = threading.Thread(
                target=run, name="warnetech-sync-runner", daemon=True
            )
            thread.start()
            ready.wait()
            self._loop, self._thread = loop, thread
            return loop

    def run(self, coro: Coroutine[Any, Any, T], *, timeout: float | None = None) -> T:
        """Run ``coro`` to completion and return its result.

        Raises:
            Whatever the coroutine raises, with its original traceback.
        """
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            # Without this the coroutine keeps running on the loop after the
            # caller has given up — a slow provider call would leak a task per
            # timeout. run_coroutine_threadsafe wires cancel() through to it.
            future.cancel()
            raise

    def close(self) -> None:
        """Stop the loop thread. Further calls to :meth:`run` restart it."""
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop, self._thread = None, None
        if loop is None:
            return

        async def _drain() -> None:
            """Cancel anything still in flight and let it unwind.

            Stopping the loop outright would strand tasks mid-await — including
            ones a caller cancelled on timeout but that have not yet processed
            the cancellation.
            """
            pending = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(_drain(), loop).result(timeout=5.0)
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5.0)
        loop.close()

    def __enter__(self) -> SyncRunner:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


#: Process-wide default, so callers that just want `embed_fn` do not each
#: spin up their own loop thread.
_default_runner: SyncRunner | None = None
_default_lock = threading.Lock()


def default_runner() -> SyncRunner:
    global _default_runner
    with _default_lock:
        if _default_runner is None:
            _default_runner = SyncRunner()
        return _default_runner
