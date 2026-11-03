"""Custom models: spec validation, Modelfile rendering, the builder, training-data
tools, the eval harness, registry wiring and the ``warnetech custom`` commands.

No Ollama and no model are involved: the Ollama CLI is faked and replies come from
a scripted provider.
"""

import argparse
import json
import random
from pathlib import Path

import pytest

from warnetech_core import AIGateway
from warnetech_core.cli_core import MAX_PARAMS
from warnetech_core.config import Settings
from warnetech_core.custom import (
    CustomModel,
    CustomModelBuilder,
    check_record,
    check_reply,
    load_cases,
    load_dataset,
    load_spec,
    load_specs,
    register_custom_models,
    render_modelfile,
    resolve_adapter,
    run_eval,
    split,
    write_split,
)
from warnetech_core.custom.dataset import MAX_CONTENT_CHARS
from warnetech_core.custom.evals import EvalCase
from warnetech_core.custom.spec import MAX_SYSTEM_CHARS
from warnetech_core.custom_commands import group
from warnetech_core.errors import ConfigError
from warnetech_core.providers.base import Provider
from warnetech_core.registry import ModelRegistry, ModelSpec
from warnetech_core.runtime import OllamaCLI
from warnetech_core.types import CompletionResponse, TokenUsage

REPO = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO / "config" / "custom_models"
EVAL_FILE = REPO / "config" / "evals" / "custom.yaml"
SAMPLE_DATA = REPO / "examples" / "custom_llm" / "dataset.sample.jsonl"


def make(**overrides):
    base = {"name": "demo", "alias": "custom.demo", "base": "llama3.2:3b"}
    return CustomModel(**{**base, **overrides})


# ---- the shipped specs --------------------------------------------------


def test_shipped_specs_cover_llama_mistral_gemma():
    specs = load_specs(SPECS_DIR)
    assert {s.base.split(":")[0] for s in specs} == {"llama3.2", "mistral", "gemma2"}
    assert len({s.alias for s in specs}) == len(specs)


# ---- spec validation ----------------------------------------------------


@pytest.mark.parametrize("bad", ["Demo", "has space", "-lead", "a" * 65, "", "x;y"])
def test_bad_names_are_rejected(bad):
    with pytest.raises(ValueError):
        make(name=bad)


def test_name_at_the_length_limit_is_accepted():
    assert make(name="a" * 64).name == "a" * 64


@pytest.mark.parametrize("bad", ["llama3.2; rm -rf /", "a b", "$(whoami)", ""])
def test_bad_base_tags_are_rejected(bad):
    with pytest.raises(ValueError):
        make(base=bad)


def test_system_prompt_cannot_close_the_block():
    with pytest.raises(ValueError, match='"""'):
        make(system='ok """\nFROM evil')


def test_system_prompt_length_boundary():
    assert len(make(system="x" * MAX_SYSTEM_CHARS).system) == MAX_SYSTEM_CHARS
    with pytest.raises(ValueError):
        make(system="x" * (MAX_SYSTEM_CHARS + 1))


def test_system_prompt_rejects_nul():
    with pytest.raises(ValueError):
        make(system="a\x00b")


def test_unknown_parameter_is_rejected():
    with pytest.raises(ValueError, match="unknown parameter"):
        make(parameters={"mirostat_hack": 1})


@pytest.mark.parametrize(
    "params",
    [
        {"temperature": 2.0},
        {"temperature": 0.0},
        {"top_p": 1.0},
        {"top_k": 1},
        {"num_ctx": 256},
        {"num_ctx": 131072},
        {"seed": 0},
    ],
)
def test_parameters_on_the_boundary_are_accepted(params):
    assert make(parameters=params).parameters == params


@pytest.mark.parametrize(
    "params",
    [
        {"temperature": 2.01},
        {"temperature": -0.01},
        {"top_p": 1.01},
        {"top_k": 0},
        {"num_ctx": 255},
        {"num_ctx": 131073},
        {"seed": -1},
    ],
)
def test_parameters_just_outside_are_rejected(params):
    with pytest.raises(ValueError, match="outside"):
        make(parameters=params)


def test_whole_number_parameters_reject_fractions_and_booleans():
    with pytest.raises(ValueError, match="whole number"):
        make(parameters={"top_k": 1.5})
    with pytest.raises(ValueError, match="boolean"):
        make(parameters={"top_k": True})


@pytest.mark.parametrize("stop", [['a"b'], ["a\nb"], [""], ["x" * 65]])
def test_bad_stop_strings_are_rejected(stop):
    with pytest.raises(ValueError):
        make(stop=stop)


def test_stop_count_boundary():
    assert len(make(stop=[f"s{i}" for i in range(8)]).stop) == 8
    with pytest.raises(ValueError, match="at most 8"):
        make(stop=[f"s{i}" for i in range(9)])


@pytest.mark.parametrize("adapter", ["/etc/passwd", "../x.gguf", "a/../../x", "a\nb"])
def test_adapter_must_stay_inside_the_spec_directory(adapter):
    with pytest.raises(ValueError, match="relative path"):
        make(adapter=adapter)


@pytest.mark.parametrize(
    "provider,ok",
    [
        ("ollama", True),
        ("ollama-gpu", True),
        ("openai", False),
        ("ollama-", False),
        ("ollama gpu", False),
    ],
)
def test_provider_is_ollama_or_a_node(provider, ok):
    if ok:
        assert make(provider=provider).provider == provider
    else:
        with pytest.raises(ValueError):
            make(provider=provider)


def test_description_cannot_span_lines():
    assert "\n" not in make(description="line one\nFROM evil").description


# ---- loading ------------------------------------------------------------


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_spec_error_names_the_file(tmp_path):
    path = _write(tmp_path, "bad.yaml", "name: Bad Name\nalias: a\nbase: x:1\n")
    with pytest.raises(ConfigError, match="bad.yaml"):
        load_spec(path)


@pytest.mark.parametrize("text", ["- a list\n", "just a string\n", "a: [unclosed\n"])
def test_load_spec_rejects_non_mappings_and_bad_yaml(tmp_path, text):
    with pytest.raises(ConfigError):
        load_spec(_write(tmp_path, "x.yaml", text))


def test_load_spec_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_spec(tmp_path / "nope.yaml")


def test_load_specs_rejects_duplicate_names_and_aliases(tmp_path):
    _write(tmp_path, "a.yaml", "name: one\nalias: custom.one\nbase: x:1\n")
    _write(tmp_path, "b.yaml", "name: one\nalias: custom.two\nbase: x:1\n")
    with pytest.raises(ConfigError, match="duplicate"):
        load_specs(tmp_path)


def test_load_specs_missing_directory_is_empty(tmp_path):
    assert load_specs(tmp_path / "absent") == []


# ---- Modelfile ----------------------------------------------------------


def test_modelfile_orders_directives_and_sorts_parameters():
    spec = make(
        description="demo",
        system="Be brief.",
        stop=["END"],
        parameters={"temperature": 0.2, "num_ctx": 4096},
    )
    text = render_modelfile(spec, adapter_path=Path("/models/lora.gguf"))
    assert text.splitlines() == [
        "# demo",
        "FROM llama3.2:3b",
        "ADAPTER /models/lora.gguf",
        "PARAMETER num_ctx 4096",
        "PARAMETER temperature 0.2",
        'PARAMETER stop "END"',
        'SYSTEM """Be brief."""',
    ]


def test_modelfile_without_optional_parts_is_just_from():
    assert render_modelfile(make()) == "FROM llama3.2:3b\n"


def test_description_injection_cannot_add_a_second_from():
    text = render_modelfile(make(description="x\nFROM evil:latest"))
    assert [ln for ln in text.splitlines() if ln.startswith("FROM")] == ["FROM llama3.2:3b"]


# ---- builder ------------------------------------------------------------


class RecordingCLI(OllamaCLI):
    def __init__(self):
        super().__init__(binary="ollama", run=self._run_fake)
        self.argv: list[str] | None = None
        self.modelfile_text: str | None = None

    def _run_fake(self, argv):
        self.argv = argv
        if argv[1] == "create":
            self.modelfile_text = Path(argv[argv.index("-f") + 1]).read_text(encoding="utf-8")
        return ""


def test_resolve_adapter_none_when_unset(tmp_path):
    assert resolve_adapter(make(), tmp_path) is None


def test_resolve_adapter_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        resolve_adapter(make(adapter="lora.gguf"), tmp_path)


def test_resolve_adapter_accepts_a_file_inside(tmp_path):
    (tmp_path / "lora.gguf").write_text("x")
    assert (
        resolve_adapter(make(adapter="lora.gguf"), tmp_path) == (tmp_path / "lora.gguf").resolve()
    )


def test_resolve_adapter_rejects_a_symlink_that_leaves_the_directory(tmp_path):
    outside = tmp_path / "outside.gguf"
    outside.write_text("secret")
    inside = tmp_path / "specs"
    inside.mkdir()
    (inside / "lora.gguf").symlink_to(outside)
    with pytest.raises(ConfigError, match="outside"):
        resolve_adapter(make(adapter="lora.gguf"), inside)


def test_create_runs_ollama_with_the_rendered_modelfile(tmp_path):
    cli = RecordingCLI()
    spec = make(system="Be brief.")
    CustomModelBuilder(cli).create(spec, tmp_path)
    assert cli.argv[:3] == ["ollama", "create", "demo"]
    assert cli.argv[3] == "-f"
    assert 'SYSTEM """Be brief."""' in cli.modelfile_text
    # The temporary Modelfile is cleaned up afterwards.
    assert not Path(cli.argv[4]).exists()


def test_create_does_not_call_ollama_when_the_adapter_is_missing(tmp_path):
    cli = RecordingCLI()
    with pytest.raises(ConfigError):
        CustomModelBuilder(cli).create(make(adapter="lora.gguf"), tmp_path)
    assert cli.argv is None


def test_create_includes_the_resolved_adapter(tmp_path):
    (tmp_path / "lora.gguf").write_text("x")
    cli = RecordingCLI()
    CustomModelBuilder(cli).create(make(adapter="lora.gguf"), tmp_path)
    assert f"ADAPTER {(tmp_path / 'lora.gguf').resolve()}" in cli.modelfile_text


# ---- registry wiring ----------------------------------------------------


def test_register_custom_models_makes_aliases_resolve():
    registry = ModelRegistry.default()
    aliases = register_custom_models(registry, SPECS_DIR)
    assert set(aliases) == {"custom.general", "custom.instruct", "custom.summarise"}
    spec = registry.resolve("custom.instruct")
    assert (spec.provider, spec.provider_model) == ("ollama", "warnetech-instruct")
    assert spec.context_window == 8192
    assert spec.cost_usd(1_000_000, 1_000_000) == 0.0


def test_a_spec_can_target_a_named_node(tmp_path):
    _write(
        tmp_path, "g.yaml", "name: gpu-model\nalias: custom.gpu\nbase: x:1\nprovider: ollama-gpu\n"
    )
    registry = ModelRegistry.default()
    register_custom_models(registry, tmp_path)
    assert registry.resolve("custom.gpu").provider == "ollama-gpu"


def test_settings_load_specs_only_when_asked(monkeypatch):
    monkeypatch.delenv("WARNETECH_CUSTOM_MODELS_DIR", raising=False)
    assert "custom.general" not in Settings.from_env().build_registry().known_names()
    monkeypatch.setenv("WARNETECH_CUSTOM_MODELS_DIR", str(SPECS_DIR))
    assert "custom.general" in Settings.from_env().build_registry().known_names()


# ---- training data ------------------------------------------------------


def rec(*pairs, system=None):
    messages = [{"role": "system", "content": system}] if system else []
    for role, text in pairs:
        messages.append({"role": role, "content": text})
    return {"messages": messages}


GOOD = rec(("user", "hi"), ("assistant", "hello"))


def test_a_good_record_has_no_problems():
    assert check_record(GOOD) == []
    assert check_record(rec(("user", "q"), ("assistant", "a"), system="s")) == []


@pytest.mark.parametrize(
    "obj",
    [
        None,
        [],
        "x",
        {},
        {"messages": "no"},
        {"messages": []},
        {"messages": [{"role": "user", "content": "only one"}]},
    ],
)
def test_malformed_records_are_rejected(obj):
    assert check_record(obj)


def test_bad_role_and_empty_content_are_reported():
    assert any("role" in p for p in check_record(rec(("robot", "x"), ("assistant", "y"))))
    assert any("non-empty" in p for p in check_record(rec(("user", "   "), ("assistant", "y"))))


def test_conversation_shape_is_enforced():
    assert any(
        "last message" in p
        for p in check_record(rec(("user", "a"), ("assistant", "b"), ("user", "c")))
    )
    assert any(
        "first non-system" in p
        for p in check_record(rec(("assistant", "a"), ("user", "b"), ("assistant", "c")))
    )
    assert any(
        "alternate" in p
        for p in check_record(rec(("user", "a"), ("user", "b"), ("assistant", "c")))
    )


def test_content_length_boundary():
    assert check_record(rec(("user", "q"), ("assistant", "a" * MAX_CONTENT_CHARS))) == []
    assert check_record(rec(("user", "q"), ("assistant", "a" * (MAX_CONTENT_CHARS + 1))))


def test_message_count_boundary():
    even = [("user" if i % 2 == 0 else "assistant", "x") for i in range(64)]
    assert check_record(rec(*even)) == []
    assert check_record(rec(*even, ("user", "x")))


@pytest.mark.parametrize(
    "secret",
    [
        "key=sk-ant-api03-" + "A" * 30,
        "AKIAIOSFODNN7EXAMPLE",
        "token ghp_" + "a" * 36,
        "-----BEGIN PRIVATE KEY-----",
    ],
)
def test_credentials_in_training_data_are_rejected(secret):
    problems = check_record(rec(("user", f"here {secret}"), ("assistant", "ok")))
    assert any("credential" in p for p in problems)


def test_load_dataset_reads_the_sample():
    data = load_dataset(SAMPLE_DATA)
    assert len(data.records) == 4
    assert data.issues == [] and data.duplicates == 0


def test_load_dataset_reports_line_numbers_and_skips_bad_lines(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text(json.dumps(GOOD) + "\n\nnot json\n" + json.dumps({"messages": []}) + "\n")
    data = load_dataset(path)
    assert len(data.records) == 1
    assert sorted({i.line for i in data.issues}) == [3, 4]


def test_load_dataset_drops_exact_duplicates(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text((json.dumps(GOOD) + "\n") * 3)
    data = load_dataset(path)
    assert len(data.records) == 1 and data.duplicates == 2


def test_load_dataset_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_dataset(tmp_path / "absent.jsonl")


def test_load_dataset_stops_at_the_record_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("warnetech_core.custom.dataset.MAX_RECORDS", 2)
    path = tmp_path / "d.jsonl"
    path.write_text(
        "".join(json.dumps(rec(("user", f"q{i}"), ("assistant", "a"))) + "\n" for i in range(5))
    )
    data = load_dataset(path)
    assert len(data.records) == 2
    assert any("stopped after" in i.message for i in data.issues)


def records(n):
    return [rec(("user", f"question {i}"), ("assistant", f"answer {i}")) for i in range(n)]


def test_split_is_deterministic_and_complete():
    items = records(200)
    train, val = split(items, val_fraction=0.25, seed=1)
    assert train == split(items, val_fraction=0.25, seed=1)[0]
    assert len(train) + len(val) == 200
    assert 0.15 < len(val) / 200 < 0.35


def test_split_assignment_does_not_depend_on_order():
    items = records(100)
    shuffled = items[:]
    random.Random(7).shuffle(shuffled)
    _, val_a = split(items, val_fraction=0.3, seed=0)
    _, val_b = split(shuffled, val_fraction=0.3, seed=0)
    key = lambda r: json.dumps(r, sort_keys=True)  # noqa: E731
    assert {key(r) for r in val_a} == {key(r) for r in val_b}


def test_split_seed_changes_the_assignment():
    items = records(200)
    assert split(items, val_fraction=0.3, seed=1)[1] != split(items, val_fraction=0.3, seed=2)[1]


def test_split_zero_fraction_keeps_everything_in_train():
    train, val = split(records(20), val_fraction=0.0)
    assert len(train) == 20 and val == []


@pytest.mark.parametrize("fraction", [1.0, -0.1, 2])
def test_split_rejects_bad_fractions(fraction):
    with pytest.raises(ConfigError):
        split(records(3), val_fraction=fraction)


def test_write_split_round_trips(tmp_path):
    items = records(60)
    train_n, val_n = write_split(items, tmp_path / "out", val_fraction=0.2, seed=0)
    assert train_n + val_n == 60
    assert len(load_dataset(tmp_path / "out" / "train.jsonl").records) == train_n
    assert len(load_dataset(tmp_path / "out" / "validation.jsonl").records) == val_n


# ---- evals --------------------------------------------------------------


def test_shipped_eval_cases_load():
    cases = load_cases(EVAL_FILE)
    assert len(cases) >= 4 and len({c.id for c in cases}) == len(cases)


def case(**kw):
    return EvalCase(id="c", prompt="p", **kw)


def test_contains_is_case_insensitive():
    assert check_reply(case(contains=("Gateway",)), "the GATEWAY routes") == []
    assert check_reply(case(contains=("gateway",)), "nothing") == ["missing 'gateway'"]


def test_not_contains_fails_when_present():
    assert check_reply(case(not_contains=("secret",)), "a Secret here") == [
        "should not contain 'secret'"
    ]


def test_regex_check():
    assert check_reply(case(regex=r"^\d+$"), "123") == []
    assert check_reply(case(regex=r"^\d+$"), "12a")


def test_max_chars_boundary():
    assert check_reply(case(max_chars=5), "12345") == []
    assert check_reply(case(max_chars=5), "123456")


@pytest.mark.parametrize(
    "text",
    [
        "cases: []\n",
        "nothing: here\n",
        "cases:\n  - {id: a}\n",
        "cases:\n  - {id: a, prompt: p}\n  - {id: a, prompt: q}\n",
        "cases:\n  - {id: a, prompt: p, expect: {regex: '('}}\n",
        "cases:\n  - {id: a, prompt: p, expect: {regex: '" + "a" * 201 + "'}}\n",
    ],
)
def test_bad_case_files_are_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        load_cases(_write(tmp_path, "e.yaml", text))


class Scripted(Provider):
    name = "scripted"

    def __init__(self, replies):
        self.replies = replies
        self.calls = 0

    async def complete(self, request, spec):
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


def scripted_gateway(replies):
    registry = ModelRegistry(
        specs=[ModelSpec(alias="chat.local", provider="scripted", provider_model="x")]
    )
    provider = Scripted(replies)
    return AIGateway({"scripted": provider}, registry=registry), provider


async def test_run_eval_scores_each_case():
    gateway, _ = scripted_gateway(["yes", "no"])
    cases = [case(contains=("yes",)), EvalCase(id="d", prompt="p", contains=("yes",))]
    report = await run_eval(gateway, "chat.local", cases)
    assert (report.passed, report.total) == (1, 2)
    assert report.pass_rate == 0.5


async def test_run_eval_limit_runs_only_the_first_n():
    gateway, provider = scripted_gateway(["ok"])
    report = await run_eval(gateway, "chat.local", [case(), case(), case()], limit=2)
    assert report.total == 2 and provider.calls == 2


def test_empty_report_has_zero_pass_rate():
    from warnetech_core.custom import EvalReport

    assert EvalReport(model="m").pass_rate == 0.0


# ---- commands -----------------------------------------------------------


def parse(tail):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    group.mount(sub)
    return parser.parse_args(["custom", *tail])


@pytest.fixture
def specs_env(monkeypatch):
    monkeypatch.setenv("WARNETECH_CUSTOM_MODELS_DIR", str(SPECS_DIR))


def test_every_command_respects_the_parameter_limit():
    assert all(len(c.params) <= MAX_PARAMS for c in group.commands())


def test_list_shows_all_three(specs_env, capsys):
    assert group.dispatch(parse(["list"])) == 0
    out = capsys.readouterr().out
    for name in ("warnetech-general", "warnetech-instruct", "warnetech-summarise"):
        assert name in out


def test_list_with_no_specs_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WARNETECH_CUSTOM_MODELS_DIR", str(tmp_path))
    assert group.dispatch(parse(["list"])) == 1


def test_show_prints_the_modelfile_by_name_or_alias(specs_env, capsys):
    assert group.dispatch(parse(["show", "warnetech-general"])) == 0
    assert "FROM llama3.2:3b" in capsys.readouterr().out
    assert group.dispatch(parse(["show", "custom.summarise"])) == 0
    assert "FROM gemma2:2b" in capsys.readouterr().out


def test_unknown_model_is_a_clean_error(specs_env, capsys):
    assert group.dispatch(parse(["show", "nope"])) == 1
    assert "no custom model named 'nope'" in capsys.readouterr().err


def test_create_calls_the_builder(specs_env, capsys):
    cli = RecordingCLI()
    assert (
        group.dispatch(parse(["create", "warnetech-instruct"]), _builder=CustomModelBuilder(cli))
        == 0
    )
    assert cli.argv[:3] == ["ollama", "create", "warnetech-instruct"]
    assert "created warnetech-instruct" in capsys.readouterr().out


def test_create_without_ollama_is_a_clean_error(specs_env, monkeypatch, capsys):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert group.dispatch(parse(["create", "warnetech-general"])) == 1
    assert "ollama" in capsys.readouterr().err


def test_dataset_command_writes_a_split(tmp_path, capsys):
    out = tmp_path / "split"
    ns = parse(["dataset", str(SAMPLE_DATA), "--out-dir", str(out), "--val-fraction", "0.5"])
    assert group.dispatch(ns) == 0
    assert (out / "train.jsonl").exists() and (out / "validation.jsonl").exists()


def test_dataset_command_writes_nothing_when_there_are_problems(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n")
    out = tmp_path / "split"
    assert group.dispatch(parse(["dataset", str(bad), "--out-dir", str(out)])) == 1
    assert not out.exists()
    assert "nothing written" in capsys.readouterr().out


def test_dataset_command_rejects_a_bad_fraction(tmp_path, capsys):
    ns = parse(["dataset", str(SAMPLE_DATA), "--out-dir", str(tmp_path), "--val-fraction", "lots"])
    assert group.dispatch(ns) == 1
    assert "val-fraction" in capsys.readouterr().err


def test_eval_command_exit_code_follows_the_result(tmp_path, capsys):
    cases = _write(tmp_path, "e.yaml", "cases:\n  - {id: a, prompt: p, expect: {contains: [ok]}}\n")
    gateway, _ = scripted_gateway(["ok"])
    assert group.dispatch(parse(["eval", str(cases)]), _gateway=gateway) == 0
    assert "1/1 passed" in capsys.readouterr().out
    gateway, _ = scripted_gateway(["nope"])
    assert group.dispatch(parse(["eval", str(cases)]), _gateway=gateway) == 1
    assert "FAIL" in capsys.readouterr().out
