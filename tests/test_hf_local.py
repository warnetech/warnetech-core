"""The local-weights backend (transformers), and the Docker and git files around it.

transformers and torch are not installed in the test environment. The provider is
exercised with stand-in tokenizer/model objects, and the real loader with a fake
``transformers`` module that records how it was called. No weights are involved.
"""

import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

from warnetech_core import AIGateway, Message
from warnetech_core.errors import ConfigError, ProviderError
from warnetech_core.providers.hf_local import TransformersProvider, load_transformers_model
from warnetech_core.registry import ModelRegistry, ModelSpec
from warnetech_core.types import CompletionRequest

REPO = Path(__file__).resolve().parent.parent

SPEC = ModelSpec(
    alias="local.mistral-hf",
    provider="transformers",
    provider_model="mistral-7b",
    context_window=50,
    max_output_tokens=64,
)


# ---- stand-ins for transformers ----------------------------------------


class FakeIds(list):
    @property
    def shape(self):
        return (1, len(self))


class FakeBatch(dict):
    def to(self, device):
        self.device = device
        return self


class FakeTokenizer:
    def __init__(self, *, template=True):
        self.chat_template = "template" if template else None
        self.rendered = None
        self.encoded_text = None

    def apply_chat_template(self, messages, tokenize, add_generation_prompt):
        assert tokenize is False and add_generation_prompt is True
        self.rendered = messages
        return "<s>" + " ".join(m["content"] for m in messages)

    def __call__(self, text, return_tensors, add_special_tokens):
        assert return_tensors == "pt" and add_special_tokens is False
        self.encoded_text = text
        return FakeBatch(input_ids=FakeIds(range(len(text.split()))))

    def decode(self, ids, skip_special_tokens):
        assert skip_special_tokens is True
        return "decoded:" + ",".join(str(i) for i in ids)


class FakeModel:
    device = "fake-device"

    def __init__(self, *, fail=None):
        self.options = None
        self.fail = fail

    def generate(self, **kwargs):
        if self.fail:
            raise self.fail
        self.options = {k: v for k, v in kwargs.items() if k != "input_ids"}
        return [list(kwargs["input_ids"]) + [901, 902, 903]]


def make_root(tmp_path, *names):
    for name in names:
        (tmp_path / name).mkdir()
        (tmp_path / name / "config.json").write_text("{}")
    return tmp_path


def provider_with(tmp_path, *, names=("mistral-7b",), model=None, tokenizer=None):
    loads = []
    model = model or FakeModel()
    tokenizer = tokenizer or FakeTokenizer()

    def loader(directory):
        loads.append(directory.name)
        return tokenizer, model

    root = make_root(tmp_path, *names)
    return TransformersProvider(root, loader=loader), loads, tokenizer, model


def request(text="hi there", **kw):
    return CompletionRequest(model="m", messages=[Message.user(text)], **kw)


# ---- locating a model folder -------------------------------------------


def test_model_dir_resolves_a_valid_folder(tmp_path):
    provider, *_ = provider_with(tmp_path)
    assert provider.model_dir("mistral-7b") == (tmp_path / "mistral-7b").resolve()


@pytest.mark.parametrize("name", ["", ".hidden", "../x", "a/b", "/etc", "a b", "x" * 101, "a\x00b"])
def test_invalid_model_names_are_refused(tmp_path, name):
    provider, *_ = provider_with(tmp_path)
    with pytest.raises(ConfigError, match="invalid local model name"):
        provider.model_dir(name)


def test_name_length_boundary(tmp_path):
    provider, *_ = provider_with(tmp_path, names=("x" * 100,))
    assert provider.model_dir("x" * 100).name == "x" * 100


def test_folder_without_config_json_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ConfigError, match="config.json"):
        TransformersProvider(tmp_path).model_dir("empty")


def test_missing_folder_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="no model at"):
        TransformersProvider(tmp_path).model_dir("absent")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_pointing_outside_the_root_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.json").write_text("{}")
    root = tmp_path / "root"
    root.mkdir()
    (root / "sneaky").symlink_to(outside)
    with pytest.raises(ConfigError, match="outside"):
        TransformersProvider(root).model_dir("sneaky")


# ---- loading and caching -----------------------------------------------


def test_nothing_is_loaded_until_first_use(tmp_path):
    _, loads, *_ = provider_with(tmp_path)
    assert loads == []


async def test_model_is_loaded_once_and_reused(tmp_path):
    provider, loads, *_ = provider_with(tmp_path)
    await provider.complete(request(), SPEC)
    await provider.complete(request(), SPEC)
    assert loads == ["mistral-7b"]


async def test_only_one_model_is_kept_in_memory(tmp_path):
    provider, loads, *_ = provider_with(tmp_path, names=("mistral-7b", "other"))
    other = ModelSpec(alias="o", provider="transformers", provider_model="other")
    await provider.complete(request(), SPEC)
    await provider.complete(request(), other)
    assert provider._loaded[0] == "other"
    await provider.complete(request(), SPEC)  # the first was released, so it reloads
    assert loads == ["mistral-7b", "other", "mistral-7b"]


async def test_previous_model_is_released_before_the_next_one_loads(tmp_path):
    # Peak memory must stay at one model while switching: nothing may still be held
    # at the moment the loader for the second model starts.
    held_at_load = []
    provider = None

    def loader(directory):
        held_at_load.append((directory.name, provider._loaded))
        return FakeTokenizer(), FakeModel()

    provider = TransformersProvider(make_root(tmp_path, "mistral-7b", "other"), loader=loader)
    other = ModelSpec(alias="o", provider="transformers", provider_model="other")
    await provider.complete(request(), SPEC)
    await provider.complete(request(), other)
    assert held_at_load == [("mistral-7b", None), ("other", None)]


async def test_aclose_releases_the_model(tmp_path):
    provider, *_ = provider_with(tmp_path)
    await provider.complete(request(), SPEC)
    await provider.aclose()
    assert provider._loaded is None


# ---- generation ---------------------------------------------------------


async def test_completion_decodes_only_the_new_tokens(tmp_path):
    provider, *_ = provider_with(tmp_path)
    response = await provider.complete(request("one two three"), SPEC)
    assert response.text == "decoded:901,902,903"
    # The fake counts words: '<s>one', 'two', 'three'.
    assert (response.usage.input_tokens, response.usage.output_tokens) == (3, 3)
    assert response.provider == "transformers"
    assert response.cost_usd == 0.0


async def test_chat_template_is_rendered_to_text_first(tmp_path):
    provider, _, tokenizer, _ = provider_with(tmp_path)
    await provider.complete(request("hello"), SPEC)
    assert tokenizer.rendered == [{"role": "user", "content": "hello"}]
    assert tokenizer.encoded_text.startswith("<s>")


async def test_models_without_a_chat_template_get_a_plain_prompt(tmp_path):
    provider, _, tokenizer, _ = provider_with(tmp_path, tokenizer=FakeTokenizer(template=False))
    await provider.complete(request("hello"), SPEC)
    assert tokenizer.encoded_text == "user: hello\nassistant:"


async def test_inputs_are_moved_to_the_models_device(tmp_path):
    provider, *_ = provider_with(tmp_path)
    seen = {}
    original = FakeBatch.to

    def spy(self, device):
        seen["device"] = device
        return original(self, device)

    FakeBatch.to = spy
    try:
        await provider.complete(request(), SPEC)
    finally:
        FakeBatch.to = original
    assert seen["device"] == "fake-device"


async def test_zero_temperature_means_greedy_decoding(tmp_path):
    provider, _, _, model = provider_with(tmp_path)
    await provider.complete(request(temperature=0.0), SPEC)
    assert model.options["do_sample"] is False
    assert "temperature" not in model.options


async def test_positive_temperature_samples(tmp_path):
    provider, _, _, model = provider_with(tmp_path)
    await provider.complete(request(temperature=0.7), SPEC)
    assert model.options["do_sample"] is True and model.options["temperature"] == 0.7


async def test_new_tokens_are_capped_by_the_spec(tmp_path):
    provider, _, _, model = provider_with(tmp_path)
    await provider.complete(request(max_tokens=1000), SPEC)
    assert model.options["max_new_tokens"] == 64
    await provider.complete(request(max_tokens=10), SPEC)
    assert model.options["max_new_tokens"] == 10


async def test_stop_strings_are_passed_with_the_tokenizer(tmp_path):
    provider, _, tokenizer, model = provider_with(tmp_path)
    await provider.complete(request(stop=["END"]), SPEC)
    assert model.options["stop_strings"] == ["END"] and model.options["tokenizer"] is tokenizer


async def test_prompt_at_the_context_limit_is_accepted_and_one_over_is_not(tmp_path):
    provider, *_ = provider_with(tmp_path)
    # "<s>" is glued to the first word, so n words -> n tokens.
    await provider.complete(request(" ".join(["w"] * 50)), SPEC)
    with pytest.raises(ProviderError, match="allows 50") as exc:
        await provider.complete(request(" ".join(["w"] * 51)), SPEC)
    assert exc.value.retryable is False


async def test_out_of_memory_is_a_non_retryable_provider_error(tmp_path):
    model = FakeModel(fail=RuntimeError("CUDA out of memory"))
    provider, *_ = provider_with(tmp_path, model=model)
    with pytest.raises(ProviderError, match="out of memory") as exc:
        await provider.complete(request(), SPEC)
    assert exc.value.retryable is False


async def test_a_missing_model_folder_surfaces_as_a_config_error(tmp_path):
    provider = TransformersProvider(tmp_path, loader=lambda d: (None, None))
    with pytest.raises(ConfigError):
        await provider.complete(request(), SPEC)


async def test_works_through_the_gateway(tmp_path):
    provider, *_ = provider_with(tmp_path)
    registry = ModelRegistry(specs=[SPEC])
    gateway = AIGateway({"transformers": provider}, registry=registry)
    reply = await gateway.complete("local.mistral-hf", "hello world")
    assert reply.text.startswith("decoded:") and reply.provider == "transformers"


def test_the_shipped_registry_entry_points_at_this_provider():
    registry = ModelRegistry.default()
    registry.load_yaml(REPO / "config" / "local_models.yaml")
    spec = registry.resolve("local.mistral-hf")
    assert (spec.provider, spec.provider_model) == ("transformers", "mistral-7b")


# ---- the real loader, against a fake transformers module ---------------


def install_fake_transformers(monkeypatch, *, reject_dtype=False):
    calls = {"tokenizer": [], "model": []}

    class Tok:
        @staticmethod
        def from_pretrained(path, **kw):
            calls["tokenizer"].append((path, kw))
            return "TOKENIZER"

    class Mod:
        @staticmethod
        def from_pretrained(path, **kw):
            calls["model"].append((path, kw))
            if reject_dtype and "dtype" in kw:
                raise TypeError("unexpected keyword argument 'dtype'")
            return "MODEL"

    module = types.ModuleType("transformers")
    module.AutoTokenizer, module.AutoModelForCausalLM = Tok, Mod
    monkeypatch.setitem(sys.modules, "transformers", module)
    return calls


def test_real_loader_never_goes_online_or_runs_remote_code(monkeypatch, tmp_path):
    calls = install_fake_transformers(monkeypatch)
    assert load_transformers_model(tmp_path) == ("TOKENIZER", "MODEL")
    for kw in (calls["tokenizer"][0][1], calls["model"][0][1]):
        assert kw["local_files_only"] is True
        assert kw["trust_remote_code"] is False
    assert calls["model"][0][1]["device_map"] == "auto"
    assert calls["model"][0][1]["dtype"] == "auto"
    assert calls["tokenizer"][0][0] == str(tmp_path)


def test_real_loader_falls_back_to_torch_dtype_on_older_transformers(monkeypatch, tmp_path):
    calls = install_fake_transformers(monkeypatch, reject_dtype=True)
    assert load_transformers_model(tmp_path)[1] == "MODEL"
    assert len(calls["model"]) == 2
    retry = calls["model"][1][1]
    assert retry["torch_dtype"] == "auto" and "dtype" not in retry
    assert retry["local_files_only"] is True and retry["trust_remote_code"] is False


def test_missing_transformers_gives_an_install_hint(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "transformers", None)  # makes the import fail
    with pytest.raises(ConfigError, match=r"warnetech-core\[hf\]"):
        load_transformers_model(tmp_path)


# ---- gateway registration ----------------------------------------------


def _clean_env(monkeypatch):
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "NVIDIA_API_KEY",
        "OLLAMA_HOST",
        "LMSTUDIO_HOST",
        "WARNETECH_HF_MODELS_DIR",
        "WARNETECH_LOCAL_ONLY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_provider_is_only_registered_when_a_models_dir_is_set(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    assert "transformers" not in AIGateway.from_env().providers
    monkeypatch.setenv("WARNETECH_HF_MODELS_DIR", str(tmp_path))
    assert "transformers" in AIGateway.from_env().providers


def test_provider_is_available_in_local_only_mode(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    monkeypatch.setenv("WARNETECH_HF_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key-for-tests")
    providers = AIGateway.from_env(local_only=True).providers
    assert "transformers" in providers and "openai" not in providers


# ---- weights never reach git or an image -------------------------------

needs_git = pytest.mark.skipif(not (REPO / ".git").exists(), reason="not a git checkout")


@needs_git
@pytest.mark.parametrize(
    "path",
    [
        "models/mistral-7b/model.safetensors",
        "models/mistral-7b/config.json",
        "elsewhere/weights.safetensors",
        "elsewhere/model.gguf",
    ],
)
def test_model_files_are_git_ignored(path):
    result = subprocess.run(
        ["git", "check-ignore", "-q", path], cwd=REPO, capture_output=True, check=False
    )
    assert result.returncode == 0, f"{path} would be committed"


def test_docker_context_excludes_weights_and_secrets():
    lines = {ln.strip() for ln in (REPO / ".dockerignore").read_text().splitlines()}
    assert {"models/", "*.safetensors", "*.gguf", ".env", ".git/"} <= lines


def test_hf_dockerfile_is_pinned_unprivileged_and_offline():
    text = (REPO / "Dockerfile.hf").read_text()
    from_line = next(ln for ln in text.splitlines() if ln.startswith("FROM "))
    assert ":" in from_line and not from_line.endswith(":latest")
    assert re.search(r"^USER app$", text, re.MULTILINE)
    assert "HF_HUB_OFFLINE=1" in text and "TRANSFORMERS_OFFLINE=1" in text
    assert "WARNETECH_LOCAL_ONLY=1" in text
    assert not re.search(r"TOKEN|SECRET|PASSWORD", text), "no credentials in the image"
    assert "models" not in re.sub(r"WARNETECH_HF_MODELS_DIR=/app/models", "", text).replace(
        "local_models", ""
    ), "weights must not be copied into the image"


def compose():
    return yaml.safe_load((REPO / "docker-compose.yml").read_text())


def test_runtime_service_is_opt_in_readonly_and_local():
    svc = compose()["services"]["llm-runtime"]
    assert svc["profiles"] == ["hf"]
    assert svc["build"]["dockerfile"] == "Dockerfile.hf"
    assert any(v.startswith("./models:") and v.endswith(":ro") for v in svc["volumes"])
    assert all(p.startswith("127.0.0.1:") for p in svc["ports"])
    assert not any("HF_TOKEN" in e for e in svc["environment"])


def test_compose_does_not_require_the_api_token_for_other_services():
    # A required-variable rule would break every `docker compose` command, because
    # compose evaluates it even when the service's profile is off.
    text = (REPO / "docker-compose.yml").read_text()
    assert "WARNETECH_API_TOKEN:?" not in text and "${WARNETECH_API_TOKEN:-}" in text


def _compose_available():
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True, check=False)
    return probe.returncode == 0


@pytest.mark.skipif(not _compose_available(), reason="docker compose is not available")
def test_compose_file_parses_with_and_without_the_profile(monkeypatch):
    monkeypatch.delenv("WARNETECH_API_TOKEN", raising=False)

    def services(*extra):
        out = subprocess.run(
            ["docker", "compose", *extra, "config", "--services"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return set(out.split())

    assert "llm-runtime" not in services()
    assert "llm-runtime" in services("--profile", "hf")
    assert {"warnetech-core", "ollama"} <= services()


# ---- packaging ----------------------------------------------------------


def test_hf_extra_is_optional_and_limited_to_python_311_plus():
    text = (REPO / "pyproject.toml").read_text()
    extra = re.search(r"^hf = \[(.*?)^\]", text, re.MULTILINE | re.DOTALL).group(1)
    requirements = re.findall(r'"([^"]+)"', extra)
    assert {r.split(">")[0] for r in requirements} == {"transformers", "torch", "accelerate"}
    assert all("python_version >= '3.11'" in r for r in requirements)
    core = re.search(r"^dependencies = \[(.*?)^\]", text, re.MULTILINE | re.DOTALL).group(1)
    assert not re.search(r"transformers|torch|accelerate", core)
