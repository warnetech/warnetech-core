"""Ollama provider, resource probing, the ollama CLI wrapper, and the CLI core.

Nothing here needs a running Ollama server or the ``ollama`` binary. The
provider uses an httpx mock transport, and the CLI wrapper takes a fake runner.
"""

import argparse
import json

import httpx
import pytest

from warnetech_core.cli import main as cli_main
from warnetech_core.cli_core import MAX_PARAMS, Command, CommandGroup, Param
from warnetech_core.errors import ConfigError
from warnetech_core.gateway import AIGateway
from warnetech_core.ollama_commands import describe_resources, group
from warnetech_core.providers.ollama import DEFAULT_HOST, OllamaProvider, ollama_base_url
from warnetech_core.registry import ModelSpec
from warnetech_core.runtime import OllamaCLI, Resources, probe_resources, recommend_model
from warnetech_core.runtime.ollama_cli import (
    parse_ollama_list,
    parse_ollama_ps,
    parse_size,
    validate_model_name,
)
from warnetech_core.runtime.resources import parse_meminfo, parse_nvidia_smi
from warnetech_core.types import CompletionRequest, Message

OLLAMA_SPEC = ModelSpec(
    alias="local.llama",
    provider="ollama",
    provider_model="llama3.1:8b",
)

MEMINFO = """MemTotal:       16384000 kB
MemFree:         1000000 kB
MemAvailable:    8192000 kB
"""

LIST_OUTPUT = """NAME                ID              SIZE      MODIFIED
llama3.1:8b         abc123def456    4.7 GB    2 days ago
qwen2.5:14b         0123456789ab    9.0 GB    5 hours ago
tiny:1b             fedcba987654    800 MB    1 week ago
"""


# ---- provider -----------------------------------------------------------


def test_base_url_defaults_to_local_v1():
    assert ollama_base_url(None) == f"{DEFAULT_HOST}/v1"


def test_base_url_accepts_bare_host_and_v1_suffix():
    assert ollama_base_url("http://gpu-box:11434") == "http://gpu-box:11434/v1"
    assert ollama_base_url("http://gpu-box:11434/v1/") == "http://gpu-box:11434/v1"
    assert ollama_base_url("gpu-box:11434") == "http://gpu-box:11434/v1"


def test_base_url_reads_ollama_host_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://lan:11434")
    assert ollama_base_url() == "http://lan:11434/v1"


def test_provider_needs_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OllamaProvider(host="http://localhost:11434")
    assert provider.name == "ollama"


async def test_ollama_complete_translates_through_openai_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "c1",
                "choices": [{"message": {"content": "hi there"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://localhost:11434/v1"
    )
    provider = OllamaProvider(host="http://localhost:11434", client=client)
    request = CompletionRequest(model="m", messages=[Message.user("hello")])

    response = await provider.complete(request, OLLAMA_SPEC)

    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["model"] == "llama3.1:8b"
    assert response.text == "hi there"
    assert response.usage.input_tokens == 12
    assert response.cost_usd == 0.0  # local inference: zero price
    assert response.provider == "ollama"


async def test_gateway_registers_ollama_only_when_host_is_set(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert "ollama" not in AIGateway.from_env().providers

    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    assert "ollama" in AIGateway.from_env().providers


# ---- host resources -----------------------------------------------------


def test_parse_meminfo_converts_kib_to_gib():
    total, available = parse_meminfo(MEMINFO)
    assert total == pytest.approx(15.62, abs=0.01)
    assert available == pytest.approx(7.81, abs=0.01)


def test_parse_meminfo_missing_fields_are_unknown_not_zero():
    assert parse_meminfo("") == (None, None)


def test_parse_nvidia_smi_first_gpu():
    assert parse_nvidia_smi("8192, 6144\n4096, 100\n") == (8.0, 6.0)


def test_parse_nvidia_smi_garbage_is_unknown():
    assert parse_nvidia_smi("no gpu here") == (None, None)


def test_probe_uses_injected_inputs_without_touching_hardware():
    res = probe_resources(
        meminfo_text=MEMINFO,
        run=lambda argv: "8192, 6144\n",
        cpu_count=8,
    )
    assert res.cpu_count == 8
    assert res.gpu_vram_free_gb == 6.0
    assert res.ram_available_gb == pytest.approx(7.81, abs=0.01)


def test_budget_prefers_vram_over_ram_and_keeps_reserve():
    gpu = Resources(
        cpu_count=4, ram_total_gb=32, ram_available_gb=20, gpu_vram_total_gb=8, gpu_vram_free_gb=6
    )
    assert gpu.budget_gb == pytest.approx(4.0)
    cpu_only = Resources(cpu_count=4, ram_total_gb=32, ram_available_gb=10)
    assert cpu_only.budget_gb == pytest.approx(8.0)


def test_fits_applies_overhead_factor():
    res = Resources(cpu_count=4, ram_total_gb=32, ram_available_gb=10)  # budget 8
    assert res.fits(6.0)  # 6 * 1.2 = 7.2 <= 8
    assert not res.fits(7.0)  # 7 * 1.2 = 8.4 > 8


def test_unknown_memory_never_claims_fit():
    res = Resources(cpu_count=4, ram_total_gb=None, ram_available_gb=None)
    assert res.budget_gb is None
    assert not res.fits(0.1)


def test_budget_never_negative():
    res = Resources(cpu_count=1, ram_total_gb=4, ram_available_gb=1)
    assert res.budget_gb == 0.0


def test_recommend_picks_largest_that_fits():
    res = Resources(cpu_count=4, ram_total_gb=32, ram_available_gb=12)  # budget 10
    pick = recommend_model(res, [("small", 1.0), ("mid", 5.0), ("big", 20.0)])
    assert pick == "mid"


def test_recommend_none_when_nothing_fits():
    res = Resources(cpu_count=4, ram_total_gb=4, ram_available_gb=2)
    assert recommend_model(res, [("big", 30.0)]) is None


def test_describe_resources_reports_unknown_plainly():
    res = Resources(cpu_count=2, ram_total_gb=None, ram_available_gb=None)
    text = describe_resources(res)
    assert "ram available:  unknown" in text


# ---- ollama CLI wrapper -------------------------------------------------


def test_parse_size_units():
    assert parse_size("4.7 GB") == 4.7
    assert parse_size("800 MB") == pytest.approx(0.781, abs=0.001)
    assert parse_size("garbage") is None


def test_parse_ollama_list_rows():
    models = parse_ollama_list(LIST_OUTPUT)
    assert [m.name for m in models] == ["llama3.1:8b", "qwen2.5:14b", "tiny:1b"]
    assert models[0].size_gb == 4.7
    assert models[0].modified == "2 days ago"


def test_parse_ollama_list_empty():
    assert parse_ollama_list("NAME  ID  SIZE  MODIFIED\n") == []


def test_parse_ollama_ps():
    out = (
        "NAME          ID     SIZE     PROCESSOR    UNTIL\n"
        "llama3.1:8b   abc    5 GB     100% GPU     4 min\n"
    )
    assert parse_ollama_ps(out) == ["llama3.1:8b"]


@pytest.mark.parametrize("bad", ["rm -rf /", "a;b", "$(whoami)", "", "x" * 300])
def test_model_name_validation_rejects_shell_like_input(bad):
    with pytest.raises(ConfigError):
        validate_model_name(bad)


def test_model_name_validation_accepts_registry_style_names():
    assert validate_model_name("llama3.1:8b") == "llama3.1:8b"
    assert validate_model_name("registry.example.com/ns/model:latest")


def test_cli_runs_fixed_argv_and_parses_list():
    calls: list[list[str]] = []

    def fake(argv):
        calls.append(argv)
        return LIST_OUTPUT

    cli = OllamaCLI(binary="/usr/bin/ollama", run=fake)
    models = cli.list_models()
    assert calls == [["/usr/bin/ollama", "list"]]
    assert len(models) == 3


def test_cli_pull_validates_name_before_running():
    calls: list[list[str]] = []
    cli = OllamaCLI(binary="ollama", run=lambda argv: calls.append(argv) or "")
    with pytest.raises(ConfigError):
        cli.pull("llama; reboot")
    assert calls == []


def test_cli_missing_binary_is_a_config_error(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(ConfigError):
        _ = OllamaCLI().binary


# ---- cli core -----------------------------------------------------------


def _noop(_values):
    return 0


def test_command_accepts_up_to_four_parameters():
    params = tuple(Param(f"p{i}") for i in range(MAX_PARAMS))
    assert len(Command("ok", "ok", _noop, params).params) == MAX_PARAMS


def test_command_rejects_more_than_four_parameters():
    params = tuple(Param(f"p{i}") for i in range(MAX_PARAMS + 1))
    with pytest.raises(ValueError, match="limit is 4"):
        Command("too-many", "x", _noop, params)


def test_command_rejects_duplicate_parameter_names():
    with pytest.raises(ValueError, match="duplicate parameter"):
        Command("dup", "x", _noop, (Param("a"), Param("a")))


def test_group_rejects_duplicate_commands():
    g = CommandGroup("g", "g")
    g.register(Command("c", "c", _noop))
    with pytest.raises(ValueError, match="duplicate command"):
        g.register(Command("c", "c", _noop))


def test_group_dispatches_parsed_values_to_handler():
    seen = {}

    def handler(values):
        seen.update(values)
        return 7

    g = CommandGroup("grp", "group")
    g.register(
        Command(
            "run",
            "run",
            handler,
            (
                Param("target"),
                Param("retries", required=False, default=2),
            ),
        )
    )
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    g.mount(sub)
    ns = parser.parse_args(["grp", "run", "thing", "--retries", "5"])
    assert g.dispatch(ns) == 7
    assert seen == {"target": "thing", "retries": "5"}


# ---- ollama command group, end to end through the warnetech CLI --------


class _FakeCLI(OllamaCLI):
    def __init__(self, responses):
        super().__init__(binary="ollama", run=self._respond)
        self._responses = responses
        self.pulled: list[str] = []

    def _respond(self, argv):
        if argv[1] == "pull":
            self.pulled.append(argv[2])
            return ""
        return self._responses[argv[1]]


def _run_ollama(argv_tail, fake):
    """Invoke a group command with an injected CLI, via the real group dispatch."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    group.mount(sub)
    ns = parser.parse_args(["ollama", *argv_tail])
    return group.dispatch(ns, _ollama=fake)


def test_ollama_models_command_lists_fit(capsys, monkeypatch):
    monkeypatch.setattr(
        "warnetech_core.ollama_commands.probe_resources",
        lambda: Resources(cpu_count=4, ram_total_gb=32, ram_available_gb=12),
    )
    fake = _FakeCLI({"list": LIST_OUTPUT})
    assert _run_ollama(["models"], fake) == 0
    out = capsys.readouterr().out
    assert "llama3.1:8b" in out
    assert "qwen2.5:14b" in out


def test_ollama_pull_takes_one_parameter(capsys):
    fake = _FakeCLI({})
    assert _run_ollama(["pull", "llama3.1:8b"], fake) == 0
    assert fake.pulled == ["llama3.1:8b"]
    assert "pulled llama3.1:8b" in capsys.readouterr().out


def test_ollama_recommend_prints_model(capsys, monkeypatch):
    monkeypatch.setattr(
        "warnetech_core.ollama_commands.probe_resources",
        lambda: Resources(cpu_count=4, ram_total_gb=32, ram_available_gb=12),
    )
    fake = _FakeCLI({"list": LIST_OUTPUT})
    assert _run_ollama(["recommend"], fake) == 0
    # Budget is 12 GB available minus 2 GB reserve = 10 GB. qwen (9.0 GB x 1.2 =
    # 10.8) does not fit; llama (4.7 x 1.2 = 5.6) does, so it is the largest fit.
    assert capsys.readouterr().out.strip() == "llama3.1:8b"


def test_warnetech_cli_mounts_ollama_group(capsys):
    # Real entry point: the group must be wired into main(). `--help` exits 0.
    with pytest.raises(SystemExit) as exc:
        cli_main(["ollama", "--help"])
    assert exc.value.code == 0
    assert "status" in capsys.readouterr().out
