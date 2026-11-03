"""Run model-written code in a child process with limits.

What this gives you, and what it does not
-----------------------------------------
The child runs with: the workspace as its working directory, a scrubbed
environment (no API keys or tokens from the parent), no stdin, a wall-clock
timeout that kills the whole process group, output caps, and POSIX resource
limits on memory, CPU time and file size.

It is **not** a sandbox. A hostile program can still read files the user can read,
use the network, and spawn processes. The real protection is human approval of
every action plus the audit log. For untrusted tasks, run the whole interpreter in
a container started with ``--network none`` and a read-only root filesystem.

Child processes are started with ``preexec_fn`` to apply the limits, which is not
safe to combine with threads that hold locks. Use one executor per process.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

from .workspace import Workspace

try:  # POSIX only; on Windows the timeout and env scrubbing still apply.
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore[assignment]

MAX_CODE_CHARS = 50_000
# The only parent variables a child may see. Anything else, including every
# credential, is dropped.
SAFE_ENV_NAMES = ("PATH", "LANG", "LC_ALL", "TZ")
_FILE_SIZE_LIMIT = 64 * 1024 * 1024


class ExecutionRefused(Exception):
    """The request broke a rule before anything ran."""


@dataclass(frozen=True)
class ExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class Executor:
    def __init__(
        self,
        workspace: Workspace,
        *,
        timeout: float = 30.0,
        max_output: int = 20_000,
        memory_mb: int = 1024,
        allowed_languages: tuple[str, ...] = ("python",),
    ) -> None:
        self.workspace = workspace
        self.timeout = timeout
        self.max_output = max_output
        self.memory_mb = memory_mb
        self.allowed_languages = allowed_languages

    # ---- building the child ------------------------------------------------

    def _argv(self, language: str, code: str) -> list[str]:
        if language == "python":
            # -E ignores PYTHON* variables, -s skips the user site directory.
            return [sys.executable, "-E", "-s", "-c", code]
        if language == "bash":
            bash = shutil.which("bash")
            if bash is None:
                raise ExecutionRefused("bash is not available on this system")
            return [bash, "--noprofile", "--norc", "-c", code]
        raise ExecutionRefused(f"unsupported language: {language!r}")

    def _env(self) -> dict[str, str]:
        env = {k: os.environ[k] for k in SAFE_ENV_NAMES if k in os.environ}
        env["HOME"] = str(self.workspace.root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def _apply_limits(self) -> None:  # runs in the child, before exec
        if resource is None:  # pragma: no cover
            return
        memory = self.memory_mb * 1024 * 1024
        cpu = int(self.timeout) + 1
        for which, value in (
            (resource.RLIMIT_AS, memory),
            (resource.RLIMIT_CPU, cpu),
            (resource.RLIMIT_FSIZE, _FILE_SIZE_LIMIT),
            (resource.RLIMIT_CORE, 0),
        ):
            try:
                resource.setrlimit(which, (value, value))
            except (ValueError, OSError):
                pass  # a platform that refuses a limit still gets the others

    # ---- running -----------------------------------------------------------

    def run(self, language: str, code: str) -> ExecResult:
        if language not in self.allowed_languages:
            raise ExecutionRefused(f"language not allowed here: {language!r}")
        if len(code) > MAX_CODE_CHARS:
            raise ExecutionRefused(f"code is longer than {MAX_CODE_CHARS} characters")
        argv = self._argv(language, code)

        started = time.perf_counter()
        timed_out = False
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(  # noqa: S603 - argv is fixed by us, no shell
                argv,
                cwd=self.workspace.root,
                env=self._env(),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,  # own process group, so a timeout kills children too
                preexec_fn=self._apply_limits if resource is not None else None,  # noqa: PLW1509
            )
            try:
                proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_group(proc)
                proc.wait()
            duration = time.perf_counter() - started
            stdout, out_cut = self._read_capped(out)
            stderr, err_cut = self._read_capped(err)

        return ExecResult(
            exit_code=None if timed_out else proc.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=out_cut or err_cut,
            duration_s=round(duration, 3),
        )

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()

    def _read_capped(self, handle) -> tuple[str, bool]:
        handle.seek(0)
        data = handle.read(self.max_output + 1)
        return data[: self.max_output].decode("utf-8", errors="replace"), len(
            data
        ) > self.max_output
