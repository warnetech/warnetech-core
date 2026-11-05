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
# Windows caps a whole command line at about 32,767 characters and the code is passed on it.
WINDOWS_MAX_CODE_CHARS = 30_000
_IS_WINDOWS = sys.platform == "win32"
# Only Linux is known to stop a runaway allocation under RLIMIT_AS. On macOS the limit can be
# accepted or refused and the allocation still succeeds (GitHub's macOS runner let a 1 GiB
# allocation through a 256 MB limit); Windows has no such limit at all.
MEMORY_LIMIT_ENFORCED = sys.platform.startswith("linux")
# Where the limit is not enforced, code is refused unless the operator opts in with this
# environment variable. It is read from the environment, so neither the model nor an MCP
# client can switch it on.
ENV_ALLOW_UNENFORCED_MEMORY = "WARNETECH_ALLOW_UNENFORCED_MEMORY"


def allow_unenforced_memory_from_env() -> bool:
    return os.environ.get(ENV_ALLOW_UNENFORCED_MEMORY, "").strip().lower() in {
        "1", "true", "yes", "on",
    }
# Not defined by subprocess on POSIX; the documented value of the Windows constant.
_CREATE_NEW_PROCESS_GROUP = 0x00000200
# The only parent variables a child may see. Anything else, including every
# credential, is dropped.
SAFE_ENV_NAMES = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR")
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
        allow_unenforced_memory: bool | None = None,
    ) -> None:
        self.workspace = workspace
        self.timeout = timeout
        self.max_output = max_output
        self.memory_mb = memory_mb
        self.allowed_languages = allowed_languages
        self.allow_unenforced_memory = (
            allow_unenforced_memory
            if allow_unenforced_memory is not None
            else allow_unenforced_memory_from_env()
        )

    @property
    def memory_limit_enforced(self) -> bool:
        """Whether ``memory_mb`` really stops a program on this platform."""
        return MEMORY_LIMIT_ENFORCED

    # ---- building the child ------------------------------------------------

    def _argv(self, language: str, code: str) -> list[str]:
        if language == "python":
            # -E ignores PYTHON* variables, -s skips the user site directory.
            return [sys.executable, "-E", "-s", "-c", code]
        if language == "bash":
            if _IS_WINDOWS:
                # `bash` on Windows often resolves to WSL, a different environment where the
                # workspace, limits and scrubbed environment would not apply.
                raise ExecutionRefused("bash is not supported on Windows; use python")
            bash = shutil.which("bash")
            if bash is None:
                raise ExecutionRefused("bash is not available on this system")
            return [bash, "--noprofile", "--norc", "-c", code]
        raise ExecutionRefused(f"unsupported language: {language!r}")

    def _env(self) -> dict[str, str]:
        env = {k: os.environ[k] for k in SAFE_ENV_NAMES if k in os.environ}
        env["HOME"] = str(self.workspace.root)
        if _IS_WINDOWS:
            env["USERPROFILE"] = str(self.workspace.root)
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
        if len(code) > self.max_code_chars:
            raise ExecutionRefused(f"code is longer than {self.max_code_chars} characters")
        if not self.memory_limit_enforced and not self.allow_unenforced_memory:
            raise ExecutionRefused(
                "the memory limit is not enforced on this platform, so code is not run; "
                f"set {ENV_ALLOW_UNENFORCED_MEMORY}=1 to allow it"
            )
        argv = self._argv(language, code)

        started = time.perf_counter()
        timed_out = False
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(  # noqa: S603 - argv is fixed by us, no shell
                    argv,
                    cwd=self.workspace.root,
                    env=self._env(),
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    **self._popen_options(),
                )
            except OSError as exc:
                raise ExecutionRefused(f"could not start the process: {exc}") from exc
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

    @property
    def max_code_chars(self) -> int:
        """The longest program accepted here; lower on Windows because of its command-line limit."""
        return WINDOWS_MAX_CODE_CHARS if _IS_WINDOWS else MAX_CODE_CHARS

    def _popen_options(self) -> dict:
        """Platform-specific process-start options.

        POSIX: a new session (so a timeout can kill the whole group) and resource limits.
        Windows: a new process group; ``preexec_fn`` and ``setrlimit`` do not exist there,
        so the memory, CPU and file-size limits are not applied.
        """
        if _IS_WINDOWS:
            return {"creationflags": _CREATE_NEW_PROCESS_GROUP}
        options: dict = {"start_new_session": True}
        if resource is not None:
            options["preexec_fn"] = self._apply_limits  # noqa: PLW1509
        return options

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        """Kill the child and everything it started."""
        if _IS_WINDOWS:
            # taskkill /T ends the whole tree; killing the child itself is the fallback.
            try:
                subprocess.run(  # noqa: S603 - fixed argv, no shell
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=10, check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                proc.kill()
            except OSError:
                pass
            return
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
