"""The interpreter: workspace confinement, execution limits, approval, the model
loop, agent tools, the LM Studio backend, the CLI, and the MCP tool.

Executor tests run real subprocesses, but only harmless code inside a temp dir.
The model is a scripted provider, so no model or network is involved.
"""

import argparse
import json
import os
import subprocess
import sys

import pytest

from warnetech_core import AIGateway
from warnetech_core.agi.tools import ToolCall, ToolRegistry
from warnetech_core.cli_core import MAX_PARAMS
from warnetech_core.interpreter import (
    Action,
    AuditLog,
    ExecutionRefused,
    Executor,
    Interpreter,
    Policy,
    Workspace,
    WorkspaceError,
    deny_all,
    first_block,
    prompt_approver,
    register_interpreter_tools,
)
from warnetech_core.interpreter import executor as executor_module
from warnetech_core.interpreter_commands import group
from warnetech_core.providers.base import Provider
from warnetech_core.providers.lmstudio import DEFAULT_HOST, LMStudioProvider, lmstudio_base_url
from warnetech_core.registry import ModelRegistry, ModelSpec
from warnetech_core.types import CompletionResponse, TokenUsage


@pytest.fixture(autouse=True)
def _allow_unenforced_memory(monkeypatch):
    # macOS and Windows do not enforce the memory limit, and code is refused there unless the
    # operator opts in. These tests exercise running code, so they opt in; the tests of the
    # refusal itself remove it again.
    monkeypatch.setenv("WARNETECH_ALLOW_UNENFORCED_MEMORY", "1")


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits")


# ---- workspace ----------------------------------------------------------


def test_workspace_resolves_inside_root(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.resolve("a/b.txt") == tmp_path.resolve() / "a" / "b.txt"


@pytest.mark.parametrize(
    "bad", ["../escape.txt", "a/../../escape.txt", "/etc/passwd", "", "a\x00b"]
)
def test_workspace_rejects_escapes_and_bad_paths(tmp_path, bad):
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path).resolve(bad)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: bash and symlinks")
def test_workspace_rejects_symlink_that_points_outside(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    root = tmp_path / "root"
    ws = Workspace(root)
    (root / "link").symlink_to(outside)
    with pytest.raises(WorkspaceError):
        ws.read_text("link/secret.txt")


def test_workspace_write_read_list_round_trip(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.write_text("notes/a.txt", "hello") == 5
    assert ws.read_text("notes/a.txt") == "hello"
    assert ws.list_dir(".") == ["notes/"]
    assert ws.list_dir("notes") == ["a.txt"]


def test_workspace_enforces_size_caps(tmp_path):
    ws = Workspace(tmp_path, max_file_bytes=10)
    with pytest.raises(WorkspaceError):
        ws.write_text("big.txt", "x" * 11)
    ws.write_text("ok.txt", "x" * 10)  # exactly at the limit is allowed
    (tmp_path / "huge.txt").write_text("x" * 11)
    with pytest.raises(WorkspaceError):
        ws.read_text("huge.txt")


def test_workspace_cannot_write_to_its_root(tmp_path):
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path).write_text(".", "x")


# ---- executor -----------------------------------------------------------


def test_python_runs_in_the_workspace(tmp_path):
    result = Executor(Workspace(tmp_path)).run("python", "import os; print(os.getcwd())")
    assert result.ok
    assert result.stdout.strip() == str(tmp_path.resolve())


def test_nonzero_exit_is_reported(tmp_path):
    result = Executor(Workspace(tmp_path)).run("python", "import sys; sys.exit(3)")
    assert result.exit_code == 3
    assert not result.ok


def test_stderr_is_captured(tmp_path):
    result = Executor(Workspace(tmp_path)).run("python", "raise ValueError('boom')")
    assert not result.ok
    assert "ValueError: boom" in result.stderr


def test_timeout_kills_the_process(tmp_path):
    ex = Executor(Workspace(tmp_path), timeout=1.0)
    result = ex.run("python", "import time; time.sleep(30)")
    assert result.timed_out
    assert result.exit_code is None
    assert result.duration_s < 10


def test_output_is_capped_and_marked_truncated(tmp_path):
    ex = Executor(Workspace(tmp_path), max_output=100)
    result = ex.run("python", "print('x' * 5000)")
    assert len(result.stdout) == 100
    assert result.truncated


def test_output_exactly_at_cap_is_not_truncated(tmp_path):
    ex = Executor(Workspace(tmp_path), max_output=100)
    result = ex.run("python", "import sys; sys.stdout.write('x' * 100)")
    assert len(result.stdout) == 100
    assert not result.truncated


def test_child_does_not_inherit_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_TOKEN", "should-not-leak")
    code = "import os; print(os.environ.get('FAKE_API_TOKEN'))"
    result = Executor(Workspace(tmp_path)).run("python", code)
    assert result.stdout.strip() == "None"


def test_child_home_is_the_workspace(tmp_path):
    result = Executor(Workspace(tmp_path)).run("python", "import os; print(os.environ['HOME'])")
    assert result.stdout.strip() == str(tmp_path.resolve())


def test_child_has_no_stdin(tmp_path):
    result = Executor(Workspace(tmp_path)).run(
        "python", "import sys; print(repr(sys.stdin.read()))"
    )
    assert result.stdout.strip() == "''"


def test_bash_is_refused_unless_allowed(tmp_path):
    with pytest.raises(ExecutionRefused):
        Executor(Workspace(tmp_path)).run("bash", "echo hi")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: bash and symlinks")
def test_bash_runs_when_allowed(tmp_path):
    ex = Executor(Workspace(tmp_path), allowed_languages=("python", "bash"))
    result = ex.run("bash", "echo hi")
    assert result.stdout.strip() == "hi"


def test_unknown_language_is_refused(tmp_path):
    ex = Executor(Workspace(tmp_path), allowed_languages=("python", "ruby"))
    with pytest.raises(ExecutionRefused):
        ex.run("ruby", "puts 1")


def test_oversized_code_is_refused(tmp_path):
    with pytest.raises(ExecutionRefused):
        ex = Executor(Workspace(tmp_path))
        ex.run("python", "#" * (ex.max_code_chars + 1))


def test_code_at_the_size_limit_is_accepted(tmp_path):
    ex = Executor(Workspace(tmp_path))
    result = ex.run("python", "#" * ex.max_code_chars)
    assert result.ok


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="the memory limit is only enforced on Linux"
)
def test_memory_limit_stops_a_runaway_allocation(tmp_path):
    ex = Executor(Workspace(tmp_path), memory_mb=256)
    result = ex.run("python", "x = bytearray(1024 * 1024 * 1024); print('allocated')")
    assert not result.ok
    assert "allocated" not in result.stdout


def test_executor_reports_whether_the_memory_limit_is_enforced(tmp_path):
    from warnetech_core.interpreter import executor as mod

    assert Executor(Workspace(tmp_path)).memory_limit_enforced is mod.MEMORY_LIMIT_ENFORCED
    assert mod.MEMORY_LIMIT_ENFORCED == sys.platform.startswith("linux")


# ---- parser -------------------------------------------------------------


def test_first_block_extracts_language_and_code():
    block = first_block("Sure:\n```python\nprint(1)\n```\ndone")
    assert block.language == "python"
    assert block.code == "print(1)"


@pytest.mark.parametrize(
    "label,expected",
    [("py", "python"), ("python3", "python"), ("sh", "bash"), ("shell", "bash"), ("BASH", "bash")],
)
def test_language_aliases(label, expected):
    assert first_block(f"```{label}\nx\n```").language == expected


def test_only_the_first_block_counts():
    text = "```python\nfirst()\n```\n```bash\nsecond\n```"
    assert first_block(text).code == "first()"


def test_no_block_returns_none():
    assert first_block("just words, no code") is None


def test_unlabelled_block_has_empty_language():
    assert first_block("```\nprint(1)\n```").language == ""


# ---- policy and approval ------------------------------------------------


def test_policy_defaults_to_ask():
    assert Policy().mode == "ask"


@pytest.mark.parametrize("kwargs", [{"mode": "yolo"}, {"max_steps": 0}, {"max_steps": 21}])
def test_policy_rejects_bad_values(kwargs):
    with pytest.raises(ValueError):
        Policy(**kwargs)


def test_policy_accepts_the_step_limits():
    assert Policy(max_steps=1).max_steps == 1
    assert Policy(max_steps=20).max_steps == 20


def test_deny_all_denies():
    assert deny_all(Action("python", "print(1)", "/w")) is False


def test_prompt_approver_denies_without_a_terminal():
    # pytest's stdin is not a TTY, so this must fail closed.
    assert prompt_approver(Action("python", "print(1)", "/w")) is False


# ---- audit log ----------------------------------------------------------


def test_audit_log_writes_json_lines_with_hash(tmp_path):
    path = tmp_path / "logs" / "audit.jsonl"
    AuditLog(path).record("executed", language="python", code="print(1)", exit_code=0)
    entry = json.loads(path.read_text().splitlines()[0])
    assert entry["event"] == "executed"
    assert len(entry["code_sha256"]) == 64
    assert entry["exit_code"] == 0


def test_audit_log_truncates_long_code_but_keeps_the_hash(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("proposed", code="a" * 5000)
    entry = json.loads(path.read_text())
    assert len(entry["code"]) == 2000
    assert len(entry["code_sha256"]) == 64


@posix_only
def test_audit_log_file_is_private(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("x")
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_audit_log_without_a_path_is_a_noop():
    AuditLog(None).record("executed", code="print(1)")  # must not raise


# ---- the model loop -----------------------------------------------------


class ScriptedModel(Provider):
    """Replies with each scripted text in turn, then repeats the last one."""

    name = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.seen: list[list[str]] = []

    async def complete(self, request, spec):
        self.seen.append([m.content for m in request.messages])
        text = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return CompletionResponse(
            id="t",
            text=text,
            model_alias=spec.alias,
            provider=self.name,
            provider_model=spec.provider_model,
            usage=TokenUsage(),
            cost_usd=0.0,
            latency_ms=0.0,
        )


def build_interpreter(
    tmp_path, replies, *, approver=deny_all, policy=None, audit=None, languages=("python",)
):
    model = ScriptedModel(replies)
    registry = ModelRegistry(
        specs=[
            ModelSpec(alias="chat.local", provider="scripted", provider_model="x"),
        ]
    )
    gateway = AIGateway({"scripted": model}, registry=registry)
    workspace = Workspace(tmp_path)
    interpreter = Interpreter(
        gateway,
        Executor(workspace, allowed_languages=languages),
        policy=policy,
        approver=approver,
        audit=audit,
    )
    return interpreter, model, workspace


def approve_all(_action):
    return True


WRITE_PROOF = "```python\nopen('proof.txt', 'w').write('ran')\n```"


async def test_plain_answer_finishes_without_running_anything(tmp_path):
    interpreter, _, _ = build_interpreter(tmp_path, ["The answer is 4."])
    report = await interpreter.run("what is 2+2?")
    assert report.stopped == "done"
    assert report.steps == []
    assert report.final == "The answer is 4."


async def test_approved_action_runs_and_output_returns_to_the_model(tmp_path):
    replies = ["```python\nprint(6 * 7)\n```", "It printed 42."]
    interpreter, model, _ = build_interpreter(tmp_path, replies, approver=approve_all)
    report = await interpreter.run("multiply")
    assert report.stopped == "done"
    assert report.steps[0].result.stdout.strip() == "42"
    feedback = model.seen[1][-1]
    assert "42" in feedback
    assert "not instructions" in feedback  # output is labelled as data


async def test_default_approver_denies_so_nothing_runs(tmp_path):
    interpreter, _, ws = build_interpreter(tmp_path, [WRITE_PROOF])
    report = await interpreter.run("write a file")
    assert report.stopped == "denied"
    assert not (ws.root / "proof.txt").exists()


async def test_dry_run_proposes_but_never_executes(tmp_path):
    interpreter, _, ws = build_interpreter(
        tmp_path,
        [WRITE_PROOF],
        approver=approve_all,
        policy=Policy(mode="dry-run"),
    )
    report = await interpreter.run("write a file")
    assert report.stopped == "dry-run"
    assert report.steps[0].action.code.startswith("open(")
    assert not (ws.root / "proof.txt").exists()


async def test_approver_sees_the_exact_code_before_it_runs(tmp_path):
    seen = []

    def approver(action):
        seen.append(action)
        return True

    interpreter, _, _ = build_interpreter(tmp_path, [WRITE_PROOF, "done"], approver=approver)
    await interpreter.run("write a file")
    assert seen[0].language == "python"
    assert "proof.txt" in seen[0].code


async def test_language_outside_the_allowlist_is_refused(tmp_path):
    reply = "```bash\ntouch proof.txt\n```"
    interpreter, _, ws = build_interpreter(tmp_path, [reply], approver=approve_all)
    report = await interpreter.run("touch a file")
    assert report.stopped == "refused"
    assert not (ws.root / "proof.txt").exists()


async def test_unlabelled_block_is_refused_not_guessed(tmp_path):
    interpreter, _, _ = build_interpreter(
        tmp_path,
        ["```\nprint(1)\n```"],
        approver=approve_all,
    )
    assert (await interpreter.run("x")).stopped == "refused"


async def test_step_limit_stops_a_runaway_loop(tmp_path):
    interpreter, model, _ = build_interpreter(
        tmp_path,
        ["```python\nprint(1)\n```"],
        approver=approve_all,
        policy=Policy(max_steps=2),
    )
    report = await interpreter.run("loop forever")
    assert report.stopped == "max-steps"
    assert len(report.steps) == 2
    assert model.calls == 2


async def test_auto_mode_runs_without_asking(tmp_path):
    # `auto` is only reachable from code, never the CLI or MCP.
    interpreter, _, ws = build_interpreter(
        tmp_path,
        [WRITE_PROOF, "done"],
        policy=Policy(mode="auto"),
    )
    await interpreter.run("write a file")
    assert (ws.root / "proof.txt").read_text() == "ran"


async def test_every_decision_is_written_to_the_audit_log(tmp_path):
    log_path = tmp_path / "audit" / "log.jsonl"
    interpreter, _, _ = build_interpreter(
        tmp_path / "ws",
        [WRITE_PROOF, "done"],
        approver=approve_all,
        audit=AuditLog(log_path),
    )
    await interpreter.run("write a file")
    events = [json.loads(line)["event"] for line in log_path.read_text().splitlines()]
    assert events == ["executed"]


async def test_denial_is_audited(tmp_path):
    log_path = tmp_path / "log.jsonl"
    interpreter, _, _ = build_interpreter(
        tmp_path / "ws",
        [WRITE_PROOF],
        audit=AuditLog(log_path),
    )
    await interpreter.run("write a file")
    assert json.loads(log_path.read_text())["event"] == "denied"


# ---- agent tools --------------------------------------------------------


async def test_file_tools_are_registered_without_exec(tmp_path):
    registry = ToolRegistry()
    names = register_interpreter_tools(registry, Workspace(tmp_path))
    assert "run_code" not in names
    assert registry.get("run_code") is None


async def test_run_code_needs_both_executor_and_approver(tmp_path):
    ws = Workspace(tmp_path)
    only_executor = ToolRegistry()
    register_interpreter_tools(only_executor, ws, executor=Executor(ws))
    assert only_executor.get("run_code") is None
    only_approver = ToolRegistry()
    register_interpreter_tools(only_approver, ws, approver=approve_all)
    assert only_approver.get("run_code") is None


async def test_agent_file_tools_round_trip_and_stay_confined(tmp_path):
    registry = ToolRegistry()
    register_interpreter_tools(registry, Workspace(tmp_path))
    wrote = await registry.execute(
        ToolCall(tool_name="workspace_write", arguments={"path": "a.txt", "content": "hi"})
    )
    assert wrote.success
    read = await registry.execute(ToolCall(tool_name="workspace_read", arguments={"path": "a.txt"}))
    assert read.output == "hi"
    escaped = await registry.execute(
        ToolCall(tool_name="workspace_read", arguments={"path": "../../etc/passwd"})
    )
    assert not escaped.success


async def test_agent_run_code_honours_the_approver(tmp_path):
    ws = Workspace(tmp_path)
    registry = ToolRegistry()
    register_interpreter_tools(registry, ws, executor=Executor(ws), approver=deny_all)
    call = ToolCall(
        tool_name="run_code",
        arguments={
            "language": "python",
            "code": "open('x.txt','w').write('1')",
        },
    )
    result = await registry.execute(call)
    assert result.output == {"approved": False}
    assert not (ws.root / "x.txt").exists()


async def test_agent_run_code_runs_when_approved(tmp_path):
    ws = Workspace(tmp_path)
    registry = ToolRegistry()
    register_interpreter_tools(registry, ws, executor=Executor(ws), approver=approve_all)
    result = await registry.execute(
        ToolCall(tool_name="run_code", arguments={"language": "python", "code": "print('ok')"})
    )
    assert result.output["approved"] is True
    assert result.output["stdout"].strip() == "ok"


# ---- LM Studio backend --------------------------------------------------


def test_lmstudio_default_url():
    assert lmstudio_base_url(None) == f"{DEFAULT_HOST}/v1"


def test_lmstudio_url_forms():
    assert lmstudio_base_url("http://box:1234") == "http://box:1234/v1"
    assert lmstudio_base_url("box:1234/v1/") == "http://box:1234/v1"


def test_lmstudio_provider_needs_no_key_and_is_named():
    assert LMStudioProvider().name == "lmstudio"


def test_gateway_registers_lmstudio_only_when_host_is_set(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NVIDIA_API_KEY", "LMSTUDIO_HOST"):
        monkeypatch.delenv(key, raising=False)
    assert "lmstudio" not in AIGateway.from_env().providers
    monkeypatch.setenv("LMSTUDIO_HOST", "http://localhost:1234")
    assert "lmstudio" in AIGateway.from_env().providers


def test_lmstudio_is_available_in_local_only_mode(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_HOST", "http://localhost:1234")
    assert "lmstudio" in AIGateway.from_env(local_only=True).providers


# ---- CLI ----------------------------------------------------------------


def test_run_command_uses_at_most_the_parameter_limit():
    run = next(c for c in group.commands() if c.name == "run")
    assert len(run.params) == MAX_PARAMS


def _parse(argv_tail):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    group.mount(sub)
    return parser.parse_args(["interpreter", *argv_tail])


def test_auto_mode_cannot_be_selected_from_the_cli(tmp_path):
    with pytest.raises(SystemExit):
        _parse(["run", "task", "--workspace", str(tmp_path), "--mode", "auto"])


def test_cli_dry_run_executes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WARNETECH_INTERPRETER_AUDIT", str(tmp_path / "audit.jsonl"))
    model = ScriptedModel([WRITE_PROOF])
    registry = ModelRegistry(
        specs=[
            ModelSpec(alias="chat.local", provider="scripted", provider_model="x"),
        ]
    )
    gateway = AIGateway({"scripted": model}, registry=registry)
    ws_dir = tmp_path / "ws"
    ns = _parse(["run", "write a file", "--workspace", str(ws_dir), "--mode", "dry-run"])
    code = group.dispatch(ns, _gateway=gateway, _approver=approve_all)
    assert code == 0
    assert not (ws_dir / "proof.txt").exists()
    assert "[dry-run]" in capsys.readouterr().out


def test_cli_ask_mode_runs_after_approval_and_returns_nonzero_when_denied(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("WARNETECH_INTERPRETER_AUDIT", str(tmp_path / "audit.jsonl"))
    registry = ModelRegistry(
        specs=[
            ModelSpec(alias="chat.local", provider="scripted", provider_model="x"),
        ]
    )
    ws_dir = tmp_path / "ws"

    denied = AIGateway({"scripted": ScriptedModel([WRITE_PROOF])}, registry=registry)
    ns = _parse(["run", "write", "--workspace", str(ws_dir)])
    assert group.dispatch(ns, _gateway=denied, _approver=deny_all) == 1
    assert not (ws_dir / "proof.txt").exists()

    allowed = AIGateway({"scripted": ScriptedModel([WRITE_PROOF, "done"])}, registry=registry)
    ns = _parse(["run", "write", "--workspace", str(ws_dir)])
    assert group.dispatch(ns, _gateway=allowed, _approver=approve_all) == 0
    assert (ws_dir / "proof.txt").read_text() == "ran"
    capsys.readouterr()


# ---- MCP tool -----------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-servers"))
os.environ.setdefault("WARNETECH_OFFLINE", "1")

from warnetech_mcp.server import _dispatch  # noqa: E402
from warnetech_mcp.tools import TOOL_SCHEMAS  # noqa: E402


def test_mcp_lists_the_interpret_tool():
    assert "warnetech_interpret" in {s["name"] for s in TOOL_SCHEMAS}


async def test_mcp_interpret_is_propose_only(tmp_path, monkeypatch):
    monkeypatch.setenv("WARNETECH_INTERPRETER_WORKSPACE", str(tmp_path))
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "warnetech_interpret",
            "arguments": {"task": "say hello", "model": "chat.default"},
        },
    }
    resp = await _dispatch(msg)
    assert resp["result"]["isError"] is False
    content = json.loads(resp["result"]["content"][0]["text"])
    assert content["executed"] is False
    assert list(tmp_path.iterdir()) == []  # nothing was created


# ---- Windows branches (selected by switching the platform flag) ---------

def test_windows_starts_a_process_group_and_uses_no_posix_hooks(monkeypatch, tmp_path):
    monkeypatch.setattr(executor_module, "_IS_WINDOWS", True)
    options = Executor(Workspace(tmp_path))._popen_options()
    assert options == {"creationflags": 0x00000200}


def test_posix_starts_a_new_session(monkeypatch, tmp_path):
    monkeypatch.setattr(executor_module, "_IS_WINDOWS", False)
    options = Executor(Workspace(tmp_path))._popen_options()
    assert options["start_new_session"] is True and "creationflags" not in options


def test_windows_kill_ends_the_whole_process_tree(monkeypatch):
    monkeypatch.setattr(executor_module, "_IS_WINDOWS", True)
    ran, killed = [], []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: ran.append(argv))

    class Child:
        pid = 4242

        def kill(self):
            killed.append(True)

    Executor._kill_group(Child())
    assert ran == [["taskkill", "/PID", "4242", "/T", "/F"]]
    assert killed == [True]


def test_windows_kill_still_kills_the_child_when_taskkill_is_missing(monkeypatch):
    monkeypatch.setattr(executor_module, "_IS_WINDOWS", True)

    def missing(*args, **kwargs):
        raise FileNotFoundError("taskkill")

    monkeypatch.setattr(subprocess, "run", missing)
    killed = []

    class Child:
        pid = 1

        def kill(self):
            killed.append(True)

    Executor._kill_group(Child())
    assert killed == [True]


def test_bash_is_refused_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(executor_module, "_IS_WINDOWS", True)
    ex = Executor(Workspace(tmp_path), allowed_languages=("python", "bash"))
    with pytest.raises(ExecutionRefused, match="not supported on Windows"):
        ex.run("bash", "echo hi")


def test_windows_environment_keeps_what_python_needs_and_still_drops_secrets(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(executor_module, "_IS_WINDOWS", True)
    monkeypatch.setenv("SYSTEMROOT", "C:\\Windows")
    monkeypatch.setenv("FAKE_API_TOKEN", "should-not-leak")
    env = Executor(Workspace(tmp_path))._env()
    assert env["SYSTEMROOT"] == "C:\\Windows"
    assert env["USERPROFILE"] == str(tmp_path.resolve())
    assert "FAKE_API_TOKEN" not in env


def test_windows_accepts_less_code_because_of_its_command_line_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(executor_module, "_IS_WINDOWS", True)
    ex = Executor(Workspace(tmp_path))
    assert ex.max_code_chars == executor_module.WINDOWS_MAX_CODE_CHARS
    assert executor_module.WINDOWS_MAX_CODE_CHARS < executor_module.MAX_CODE_CHARS
    with pytest.raises(ExecutionRefused, match="longer than 30000"):
        ex.run("python", "#" * (ex.max_code_chars + 1))  # refused before any process starts


def test_posix_keeps_the_larger_code_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(executor_module, "_IS_WINDOWS", False)
    assert Executor(Workspace(tmp_path)).max_code_chars == executor_module.MAX_CODE_CHARS


def test_a_process_that_cannot_start_is_a_clean_refusal(monkeypatch, tmp_path):
    def cannot_start(*args, **kwargs):
        raise OSError("[WinError 206] The filename or extension is too long")

    monkeypatch.setattr(subprocess, "Popen", cannot_start)
    with pytest.raises(ExecutionRefused, match="could not start the process"):
        Executor(Workspace(tmp_path)).run("python", "print(1)")


@pytest.mark.parametrize("enforced", [True, False])
def test_cli_warns_only_when_the_memory_limit_is_not_enforced(
    tmp_path, monkeypatch, capsys, enforced
):
    monkeypatch.setenv("WARNETECH_INTERPRETER_AUDIT", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(executor_module, "MEMORY_LIMIT_ENFORCED", enforced)
    registry = ModelRegistry(
        specs=[ModelSpec(alias="chat.local", provider="scripted", provider_model="x")]
    )
    gateway = AIGateway({"scripted": ScriptedModel([WRITE_PROOF])}, registry=registry)
    ns = _parse(["run", "write a file", "--workspace", str(tmp_path / "ws"),
                 "--mode", "dry-run"])
    assert group.dispatch(ns, _gateway=gateway, _approver=approve_all) == 0
    err = capsys.readouterr().err
    assert ("memory limit is not enforced" in err) is (not enforced)


def test_code_is_refused_where_the_memory_limit_is_not_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(executor_module, "MEMORY_LIMIT_ENFORCED", False)
    monkeypatch.delenv("WARNETECH_ALLOW_UNENFORCED_MEMORY")
    ws = Workspace(tmp_path)
    with pytest.raises(ExecutionRefused, match="WARNETECH_ALLOW_UNENFORCED_MEMORY"):
        Executor(ws).run("python", "open('proof.txt', 'w').write('ran')")
    assert not (tmp_path / "proof.txt").exists()  # nothing was started


def test_the_operator_can_opt_in_through_the_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(executor_module, "MEMORY_LIMIT_ENFORCED", False)
    assert Executor(Workspace(tmp_path)).run("python", "print('ran')").stdout.strip() == "ran"


@pytest.mark.parametrize("value", ["", "0", "no", "off", "false"])
def test_only_a_clear_yes_opts_in(tmp_path, monkeypatch, value):
    monkeypatch.setattr(executor_module, "MEMORY_LIMIT_ENFORCED", False)
    monkeypatch.setenv("WARNETECH_ALLOW_UNENFORCED_MEMORY", value)
    with pytest.raises(ExecutionRefused):
        Executor(Workspace(tmp_path)).run("python", "print(1)")


def test_an_explicit_argument_beats_the_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(executor_module, "MEMORY_LIMIT_ENFORCED", False)
    with pytest.raises(ExecutionRefused):
        Executor(Workspace(tmp_path), allow_unenforced_memory=False).run("python", "print(1)")
    assert Executor(Workspace(tmp_path), allow_unenforced_memory=True).run(
        "python", "print(2)"
    ).ok


def test_no_opt_in_is_needed_where_the_limit_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(executor_module, "MEMORY_LIMIT_ENFORCED", True)
    monkeypatch.delenv("WARNETECH_ALLOW_UNENFORCED_MEMORY")
    assert Executor(Workspace(tmp_path)).run("python", "print(3)").ok


def test_cli_run_is_refused_with_a_clear_note_and_runs_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WARNETECH_INTERPRETER_AUDIT", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(executor_module, "MEMORY_LIMIT_ENFORCED", False)
    monkeypatch.delenv("WARNETECH_ALLOW_UNENFORCED_MEMORY")
    registry = ModelRegistry(
        specs=[ModelSpec(alias="chat.local", provider="scripted", provider_model="x")]
    )
    gateway = AIGateway({"scripted": ScriptedModel([WRITE_PROOF])}, registry=registry)
    ws_dir = tmp_path / "ws"
    ns = _parse(["run", "write a file", "--workspace", str(ws_dir)])
    group.dispatch(ns, _gateway=gateway, _approver=approve_all)
    captured = capsys.readouterr()
    assert "will be refused" in captured.err and "WARNETECH_ALLOW_UNENFORCED_MEMORY" in captured.err
    assert not (ws_dir / "proof.txt").exists()
