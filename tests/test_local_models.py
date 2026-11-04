"""Local model catalogue, multi-node Ollama routing, local-only mode, and the
fallback fix. Everything runs offline: the Ollama CLI is faked and no server is
contacted.
"""

from pathlib import Path

import pytest

from warnetech_core import AIGateway, Message
from warnetech_core.config import Settings
from warnetech_core.errors import ConfigError, ModelNotRegistered
from warnetech_core.ollama_commands import group
from warnetech_core.providers.echo import EchoProvider
from warnetech_core.providers.ollama import parse_nodes
from warnetech_core.registry import ModelRegistry, ModelSpec
from warnetech_core.runtime import OllamaCLI, Resources
from warnetech_core.runtime.local_models import (
    LocalModel,
    load_catalogue,
    plan,
    resolve_install_target,
)

CATALOGUE_FILE = Path(__file__).resolve().parent.parent / "config" / "local_models.yaml"

BIG_HOST = Resources(cpu_count=8, ram_total_gb=64, ram_available_gb=40)
SMALL_HOST = Resources(cpu_count=2, ram_total_gb=4, ram_available_gb=4)


# ---- the shipped catalogue ----------------------------------------------


def test_shipped_catalogue_covers_llama_mistral_gemma():
    models = load_catalogue(CATALOGUE_FILE)
    tags = {m.model.split(":")[0] for m in models}
    assert {"llama3.2", "mistral", "gemma2"} <= tags


def test_shipped_catalogue_registers_local_aliases():
    registry = ModelRegistry.default()
    registry.load_yaml(CATALOGUE_FILE)
    spec = registry.resolve("chat.local")
    assert spec.provider == "ollama"
    assert spec.provider_model == "llama3.2:3b"
    assert spec.cost_usd(1_000_000, 1_000_000) == 0.0  # local is free


def test_local_models_file_is_loaded_by_settings(monkeypatch):
    monkeypatch.setenv("WARNETECH_LOCAL_MODELS", str(CATALOGUE_FILE))
    registry = Settings.from_env().build_registry()
    assert registry.resolve("extract.local").provider_model == "mistral:7b"


def test_local_models_file_is_not_loaded_unless_asked(monkeypatch):
    monkeypatch.delenv("WARNETECH_LOCAL_MODELS", raising=False)
    registry = Settings.from_env().build_registry()
    assert "chat.local" not in registry.aliases()


# ---- catalogue parsing --------------------------------------------------


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "local.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_duplicate_names_are_rejected(tmp_path):
    path = _write(
        tmp_path,
        """
local:
  - {name: a, model: x:1, role: r, approx_gb: 1}
  - {name: a, model: y:1, role: r, approx_gb: 1}
""",
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_catalogue(path)


def test_entry_missing_size_is_rejected(tmp_path):
    path = _write(tmp_path, "local:\n  - {name: a, model: x:1}\n")
    with pytest.raises(ConfigError, match="bad local model entry"):
        load_catalogue(path)


def test_entry_with_shell_metacharacters_in_tag_is_rejected(tmp_path):
    path = _write(tmp_path, "local:\n  - {name: a, model: 'x; rm -rf /', approx_gb: 1}\n")
    with pytest.raises(ConfigError):
        load_catalogue(path)


def test_empty_catalogue_is_valid(tmp_path):
    assert load_catalogue(_write(tmp_path, "models: []\n")) == []


# ---- planning and install targets ---------------------------------------


def test_plan_marks_fit_per_host():
    catalogue = [LocalModel("big", "x:70b", "r", 20.0), LocalModel("tiny", "y:1b", "r", 1.0)]
    big = {r.model.name: r.fits for r in plan(catalogue, BIG_HOST)}
    small = {r.model.name: r.fits for r in plan(catalogue, SMALL_HOST)}
    assert big == {"big": True, "tiny": True}
    assert small == {"big": False, "tiny": True}


def test_install_target_role_maps_to_tag():
    catalogue = [LocalModel("general", "llama3.2:3b", "r", 2.0)]
    assert resolve_install_target(catalogue, "general") == "llama3.2:3b"


def test_install_target_accepts_raw_valid_tag():
    assert resolve_install_target([], "mistral:7b") == "mistral:7b"


def test_install_target_rejects_bad_raw_tag():
    with pytest.raises(ConfigError):
        resolve_install_target([], "mistral:7b && curl evil")


# ---- multi-node routing -------------------------------------------------


def test_parse_nodes_names_providers_per_machine():
    nodes = parse_nodes("gpu=http://10.0.0.5:11434, laptop=http://10.0.0.9:11434/v1")
    assert nodes == {
        "ollama-gpu": "http://10.0.0.5:11434/v1",
        "ollama-laptop": "http://10.0.0.9:11434/v1",
    }


def test_parse_nodes_empty_is_no_nodes():
    assert parse_nodes(None) == {}
    assert parse_nodes("") == {}


@pytest.mark.parametrize("bad", ["gpu", "=http://x", "Gpu=http://x", "gpu=", "a b=http://x"])
def test_parse_nodes_rejects_malformed(bad):
    with pytest.raises(ConfigError):
        parse_nodes(bad)


def test_from_env_registers_each_node(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("OLLAMA_NODES", "gpu=http://10.0.0.5:11434")
    gateway = AIGateway.from_env()
    assert "ollama-gpu" in gateway.providers
    assert gateway.providers["ollama-gpu"]._base_url == "http://10.0.0.5:11434/v1"
    # Usage is attributed by provider name, so each node must report under its own.
    assert gateway.providers["ollama-gpu"].name == "ollama-gpu"


# ---- local-only mode ----------------------------------------------------


def _clear_hosted_keys(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NVIDIA_API_KEY", "WARNETECH_LOCAL_ONLY"):
        monkeypatch.delenv(key, raising=False)


def test_local_only_registers_no_hosted_provider(monkeypatch):
    _clear_hosted_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key-for-tests")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    gateway = AIGateway.from_env(local_only=True)
    assert "openai" not in gateway.providers
    assert "ollama" in gateway.providers
    assert "echo" in gateway.providers


def test_local_only_from_environment(monkeypatch):
    _clear_hosted_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key-for-tests")
    monkeypatch.setenv("WARNETECH_LOCAL_ONLY", "1")
    assert "openai" not in AIGateway.from_env().providers


def test_settings_carry_local_only_flag(monkeypatch):
    monkeypatch.setenv("WARNETECH_LOCAL_ONLY", "true")
    assert Settings.from_env().local_only is True


async def test_fallback_skips_unregistered_provider_instead_of_crashing():
    # Primary points at a hosted provider that is not registered (no key, or
    # local-only). The chain must fall through to the local alias, not KeyError.
    registry = ModelRegistry(
        specs=[
            ModelSpec(
                alias="hosted", provider="openai", provider_model="gpt-x", fallbacks=("local",)
            ),
            ModelSpec(alias="local", provider="echo", provider_model="echo-chat"),
        ]
    )
    gateway = AIGateway({"echo": EchoProvider()}, registry=registry)
    response = await gateway.complete("hosted", [Message.user("hello")])
    assert response.provider == "echo"


async def test_fallback_error_is_reported_when_nothing_is_registered():
    registry = ModelRegistry(
        specs=[
            ModelSpec(alias="hosted", provider="openai", provider_model="gpt-x"),
        ]
    )
    gateway = AIGateway({"echo": EchoProvider()}, registry=registry)
    with pytest.raises(ModelNotRegistered):
        await gateway.complete("hosted", [Message.user("hello")])


# ---- ollama plan / install commands -------------------------------------


class _PullRecorder(OllamaCLI):
    def __init__(self):
        super().__init__(binary="ollama", run=self._respond)
        self.pulled: list[str] = []

    def _respond(self, argv):
        if argv[1] == "pull":
            self.pulled.append(argv[2])
        return ""


def _run(argv_tail, fake, monkeypatch=None):
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    group.mount(sub)
    ns = parser.parse_args(["ollama", *argv_tail])
    return group.dispatch(ns, _ollama=fake)


def test_install_by_role_pulls_the_mapped_tag(monkeypatch, capsys):
    monkeypatch.setenv("WARNETECH_LOCAL_MODELS", str(CATALOGUE_FILE))
    fake = _PullRecorder()
    assert _run(["install", "summarise"], fake) == 0
    assert fake.pulled == ["gemma2:2b"]
    assert "role: summarise" in capsys.readouterr().out


def test_install_rejects_injection_before_pulling(monkeypatch):
    monkeypatch.setenv("WARNETECH_LOCAL_MODELS", str(CATALOGUE_FILE))
    fake = _PullRecorder()
    with pytest.raises(ConfigError):
        _run(["install", "gemma2:2b; reboot"], fake)
    assert fake.pulled == []


def test_plan_prints_every_role(monkeypatch, capsys):
    monkeypatch.setenv("WARNETECH_LOCAL_MODELS", str(CATALOGUE_FILE))
    monkeypatch.setattr("warnetech_core.ollama_commands.probe_resources", lambda: BIG_HOST)
    assert _run(["plan"], _PullRecorder()) == 0
    out = capsys.readouterr().out
    for role in ("general", "instruction", "summarise"):
        assert role in out
