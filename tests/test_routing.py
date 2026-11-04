"""Task classification, routing, pipelines, node health, and the route commands.

Everything runs offline. Model replies come from a scripted provider and the
Ollama hosts are faked with an httpx mock transport.
"""

import argparse
from pathlib import Path

import httpx
import pytest

from warnetech_core import AIGateway
from warnetech_core.cli_core import MAX_PARAMS, report_config_errors
from warnetech_core.custom import register_custom_models
from warnetech_core.errors import ConfigError
from warnetech_core.ollama_commands import group as ollama_group
from warnetech_core.providers.base import Provider
from warnetech_core.registry import ModelRegistry, ModelSpec
from warnetech_core.routing import (
    KINDS,
    RoutingError,
    TaskRouter,
    classify,
    load_pipeline,
    load_table,
    render,
    run_pipeline,
)
from warnetech_core.routing.pipeline import (
    MAX_CARRY_CHARS,
    MAX_INPUT_CHARS,
    MAX_STEPS,
    Pipeline,
    Step,
)
from warnetech_core.routing_commands import group as route_group
from warnetech_core.runtime.nodes import check_node, check_nodes
from warnetech_core.types import CompletionResponse, TokenUsage

REPO = Path(__file__).resolve().parent.parent
ROUTING_FILE = REPO / "config" / "routing.yaml"
PIPELINE_FILE = REPO / "config" / "pipelines" / "summarise-then-extract.yaml"


# ---- classification -----------------------------------------------------


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Please summarise this article", "summarise"),
        ("TL;DR of the report?", "summarise"),
        ("Give me the key points", "summarise"),
        ("Extract all the dates", "extract"),
        ("Reply with JSON only", "extract"),
        ("Convert this to CSV", "extract"),
        ("Why does my function crash?", "code"),
        ("Here is the traceback", "code"),
        ("What is the capital of France?", "chat"),
        ("", "chat"),
    ],
)
def test_classify_kinds(text, kind):
    assert classify(text).kind == kind


def test_a_code_block_wins_over_other_hints():
    text = "Summarise this:\n```python\nprint(1)\n```"
    assert classify(text).kind == "code"


def test_classification_is_case_insensitive():
    assert classify("SUMMARISE THIS").kind == "summarise"


def test_words_that_merely_contain_a_keyword_do_not_match():
    assert classify("I enjoy debugging on weekends").kind == "chat"


def test_only_the_start_of_the_text_is_examined():
    assert classify("x" * 1990 + " summarise").kind == "summarise"  # ends at the limit
    assert classify("x" * 1999 + " summarise").kind == "chat"  # starts past it


def test_every_classification_has_a_reason():
    assert classify("summarise").reason and classify("hello").reason


# ---- the router ---------------------------------------------------------

TABLE = {
    "summarise": ["hosted.sum", "local.sum"],
    "extract": ["local.ext"],
    "chat": ["local.chat"],
}


def test_first_available_alias_is_chosen():
    router = TaskRouter(TABLE)
    assert router.route("summarise this").alias == "hosted.sum"


def test_unavailable_aliases_are_skipped_with_a_record():
    router = TaskRouter(TABLE, is_available=lambda a: a.startswith("local."))
    route = router.route("summarise this")
    assert route.alias == "local.sum"
    assert route.skipped == (("hosted.sum", "provider not available here"),)


def test_no_available_model_is_an_error_naming_what_was_tried():
    router = TaskRouter(TABLE, is_available=lambda _a: False)
    with pytest.raises(RoutingError, match="hosted.sum, local.sum"):
        router.route("summarise this")


def test_explicit_kind_overrides_the_classifier():
    route = TaskRouter(TABLE).route("summarise this", kind="extract")
    assert (route.kind, route.alias) == ("extract", "local.ext")
    assert route.reason == "kind given explicitly"


def test_unknown_explicit_kind_is_refused():
    with pytest.raises(RoutingError, match="no route for kind"):
        TaskRouter(TABLE).route("x", kind="poetry")


def test_a_kind_with_no_table_entry_falls_back_to_the_default():
    route = TaskRouter(TABLE).route("Why does my function crash?")  # classified as code
    assert route.kind == "chat"
    assert "using chat" in route.reason


def test_default_must_be_in_the_table():
    with pytest.raises(RoutingError):
        TaskRouter(TABLE, default="code")


class FixedReply(Provider):
    """Replies with a fixed text per provider_model and records the prompts it saw."""

    name = "scripted"

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, request, spec):
        prompt = request.messages[-1].content
        self.prompts.append((spec.provider_model, prompt))
        return CompletionResponse(
            id="t",
            text=self.replies.get(spec.provider_model, "ok"),
            model_alias=spec.alias,
            provider=self.name,
            provider_model=spec.provider_model,
            usage=TokenUsage(),
            cost_usd=0.0,
            latency_ms=0.0,
        )


def make_gateway(models, provider=None):
    registry = ModelRegistry(
        specs=[
            ModelSpec(alias=alias, provider=prov, provider_model=alias)
            for alias, prov in models.items()
        ]
    )
    provider = provider or FixedReply()
    return AIGateway({"scripted": provider}, registry=registry), provider


def test_for_gateway_skips_aliases_whose_provider_is_not_registered():
    gateway, _ = make_gateway({"hosted.sum": "openai", "local.sum": "scripted"})
    router = TaskRouter.for_gateway(
        gateway, {"summarise": ["hosted.sum", "local.sum"], "chat": ["local.sum"]}
    )
    assert router.route("summarise this").alias == "local.sum"


def test_for_gateway_treats_unknown_aliases_as_unavailable():
    gateway, _ = make_gateway({"local.sum": "scripted"})
    router = TaskRouter.for_gateway(
        gateway, {"summarise": ["not.registered", "local.sum"], "chat": ["local.sum"]}
    )
    assert router.route("summarise this").alias == "local.sum"


def test_local_only_mode_routes_around_hosted_models(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key-for-tests")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    registry = ModelRegistry.default()
    register_custom_models(registry, REPO / "config" / "custom_models")
    table = {"summarise": ["openai.gpt-4o-mini", "custom.summarise"], "chat": ["custom.general"]}

    hosted = TaskRouter.for_gateway(AIGateway.from_env(registry=registry), table)
    assert hosted.route("summarise this").alias == "openai.gpt-4o-mini"

    local = TaskRouter.for_gateway(AIGateway.from_env(registry=registry, local_only=True), table)
    route = local.route("summarise this")
    assert route.alias == "custom.summarise"
    assert route.skipped[0][0] == "openai.gpt-4o-mini"


# ---- routing table files ------------------------------------------------


def _yaml(tmp_path, text):
    path = tmp_path / "t.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_table_loads_and_every_alias_resolves():
    table, default = load_table(ROUTING_FILE)
    assert default in table and set(table) <= set(KINDS)
    registry = ModelRegistry.default()
    registry.load_yaml(REPO / "config" / "local_models.yaml")
    register_custom_models(registry, REPO / "config" / "custom_models")
    for kind, aliases in table.items():
        for alias in aliases:
            assert registry.resolve(alias), f"{kind}: {alias} does not resolve"


@pytest.mark.parametrize(
    "text",
    [
        "default: chat\n",
        "tasks: []\n",
        "tasks: {poetry: [a.b]}\n",
        "tasks: {chat: []}\n",
        "tasks: {chat: ['bad alias!']}\n",
        "tasks: {chat: [a.b]}\ndefault: code\n",
        "tasks: {chat: [3]}\n",
    ],
)
def test_bad_routing_tables_are_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        load_table(_yaml(tmp_path, text))


def test_missing_routing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_table(tmp_path / "absent.yaml")


# ---- pipelines ----------------------------------------------------------


def test_shipped_pipeline_loads():
    pipeline = load_pipeline(PIPELINE_FILE)
    assert [s.name for s in pipeline.steps] == ["summarise", "extract"]
    assert [s.model for s in pipeline.steps] == ["route:summarise", "route:extract"]


@pytest.mark.parametrize(
    "text",
    [
        "name: x\n",
        "steps: []\n",
        "steps:\n  - {name: a, model: m}\n",
        "steps:\n  - {name: a, model: m, prompt: p}\n  - {name: a, model: m, prompt: p}\n",
    ],
)
def test_bad_pipelines_are_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        load_pipeline(_yaml(tmp_path, text))


def test_step_count_boundary(tmp_path):
    def body(n):
        return "steps:\n" + "".join(f"  - {{name: s{i}, model: m, prompt: p}}\n" for i in range(n))

    assert len(load_pipeline(_yaml(tmp_path, body(MAX_STEPS))).steps) == MAX_STEPS
    with pytest.raises(ConfigError, match="more than"):
        load_pipeline(_yaml(tmp_path, body(MAX_STEPS + 1)))


def test_render_fills_both_placeholders_and_leaves_other_braces():
    out = render('A={input} B={previous} J={"k": 1}', original="orig", previous="prev")
    assert out == 'A=orig B=prev J={"k": 1}'


def test_render_does_not_evaluate_the_template():
    assert render("{input.__class__}", original="x", previous="") == "{input.__class__}"


def test_text_containing_a_placeholder_is_not_substituted_again():
    # A user's text that happens to contain "{previous}" must stay literal.
    out = render("{input}", original="keep {previous} as written", previous="SECRET-OUTPUT")
    assert out == "keep {previous} as written"


async def test_pipeline_passes_each_output_to_the_next_step():
    gateway, provider = make_gateway(
        {"one": "scripted", "two": "scripted"}, FixedReply({"one": "first-out"})
    )
    pipeline = Pipeline("p", (Step("a", "one", "do {input}"), Step("b", "two", "then {previous}")))
    result = await run_pipeline(gateway, pipeline, "the text")
    assert provider.prompts == [("one", "do the text"), ("two", "then first-out")]
    assert [s.output for s in result.steps] == ["first-out", "ok"]
    assert result.final == "ok"


async def test_route_steps_use_the_router():
    gateway, provider = make_gateway(
        {"local.sum": "scripted", "local.ext": "scripted", "local.chat": "scripted"}
    )
    router = TaskRouter.for_gateway(
        gateway, {"summarise": ["local.sum"], "extract": ["local.ext"], "chat": ["local.chat"]}
    )
    pipeline = Pipeline(
        "p", (Step("a", "route:summarise", "{input}"), Step("b", "route:extract", "{previous}"))
    )
    await run_pipeline(gateway, pipeline, "text", router=router)
    assert [m for m, _ in provider.prompts] == ["local.sum", "local.ext"]


async def test_route_step_without_a_router_is_an_error():
    gateway, _ = make_gateway({"m": "scripted"})
    with pytest.raises(ConfigError, match="no router"):
        await run_pipeline(gateway, Pipeline("p", (Step("a", "route:chat", "{input}"),)), "x")


async def test_input_length_boundary():
    gateway, _ = make_gateway({"m": "scripted"})
    pipeline = Pipeline("p", (Step("a", "m", "{input}"),))
    await run_pipeline(gateway, pipeline, "x" * MAX_INPUT_CHARS)  # at the limit: fine
    with pytest.raises(ConfigError, match="longer than"):
        await run_pipeline(gateway, pipeline, "x" * (MAX_INPUT_CHARS + 1))


async def test_only_a_bounded_amount_of_output_is_carried_forward():
    gateway, provider = make_gateway(
        {"one": "scripted", "two": "scripted"}, FixedReply({"one": "y" * (MAX_CARRY_CHARS + 500)})
    )
    pipeline = Pipeline("p", (Step("a", "one", "{input}"), Step("b", "two", "{previous}")))
    await run_pipeline(gateway, pipeline, "x")
    assert len(provider.prompts[1][1]) == MAX_CARRY_CHARS


# ---- node health --------------------------------------------------------


def mock_http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_node_reports_sorted_models_and_strips_v1():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"models": [{"name": "b:1"}, {"name": "a:1"}]})

    status = await check_node("gpu", "http://10.0.0.5:11434/v1", client=mock_http(handler))
    assert seen["url"] == "http://10.0.0.5:11434/api/tags"
    assert status.reachable and status.models == ("a:1", "b:1")


async def test_node_with_no_models_is_still_reachable():
    status = await check_node(
        "n", "http://h", client=mock_http(lambda r: httpx.Response(200, json={"models": []}))
    )
    assert status.reachable and status.models == ()


async def test_node_http_error_is_reported():
    status = await check_node("n", "http://h", client=mock_http(lambda r: httpx.Response(503)))
    assert (status.reachable, status.error) == (False, "HTTP 503")


@pytest.mark.parametrize("body", ["not json", "[1, 2]", '{"models": [{"nope": 1}]}'])
async def test_node_with_an_unexpected_body_is_reported(body):
    status = await check_node(
        "n", "http://h", client=mock_http(lambda r: httpx.Response(200, text=body))
    )
    assert (status.reachable, status.error) == (False, "unexpected response body")


async def test_unreachable_node_does_not_raise():
    def handler(request):
        raise httpx.ConnectError("refused")

    status = await check_node("n", "http://h", client=mock_http(handler))
    assert (status.reachable, status.error) == (False, "ConnectError")


async def test_check_nodes_keeps_order_with_mixed_results():
    def handler(request):
        if "bad" in request.url.host:
            return httpx.Response(500)
        return httpx.Response(200, json={"models": [{"name": "m"}]})

    statuses = await check_nodes({"a": "http://good", "b": "http://bad"}, client=mock_http(handler))
    assert [(s.name, s.reachable) for s in statuses] == [("a", True), ("b", False)]


def run_ollama(tail, **context):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    ollama_group.mount(sub)
    return ollama_group.dispatch(parser.parse_args(["ollama", *tail]), **context)


def test_nodes_command_lists_hosts(monkeypatch, capsys):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("OLLAMA_NODES", "gpu=http://10.0.0.5:11434")
    client = mock_http(lambda r: httpx.Response(200, json={"models": [{"name": "llama3.2:3b"}]}))
    assert run_ollama(["nodes"], _http=client) == 0
    out = capsys.readouterr().out
    assert "ollama-gpu" in out and "up" in out and "llama3.2:3b" in out


def test_nodes_command_exits_nonzero_when_a_host_is_down(monkeypatch, capsys):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("OLLAMA_NODES", "gpu=http://10.0.0.5:11434")
    assert run_ollama(["nodes"], _http=mock_http(lambda r: httpx.Response(500))) == 1
    assert "down (HTTP 500)" in capsys.readouterr().out


def test_nodes_command_with_nothing_configured(monkeypatch, capsys):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_NODES", raising=False)
    assert run_ollama(["nodes"]) == 1
    assert "no Ollama hosts configured" in capsys.readouterr().out


# ---- commands -----------------------------------------------------------


def parse(tail):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    route_group.mount(sub)
    return parser.parse_args(["route", *tail])


@pytest.fixture
def routed(tmp_path, monkeypatch):
    """A gateway with scripted local models and a routing file that points at them."""
    table = tmp_path / "routing.yaml"
    table.write_text(
        "default: chat\ntasks:\n  summarise: [hosted.sum, local.sum]\n  chat: [local.chat]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WARNETECH_ROUTING_FILE", str(table))
    provider = FixedReply({"local.sum": "a short summary", "local.chat": "hello back"})
    gateway, _ = make_gateway(
        {"hosted.sum": "openai", "local.sum": "scripted", "local.chat": "scripted"}, provider
    )
    return gateway, provider


def test_route_commands_respect_the_parameter_limit():
    assert all(len(c.params) <= MAX_PARAMS for c in route_group.commands())


def test_explain_shows_kind_model_and_skips(routed, capsys):
    gateway, _ = routed
    assert route_group.dispatch(parse(["explain", "Summarise this"]), _gateway=gateway) == 0
    out = capsys.readouterr().out
    assert "kind:   summarise" in out
    assert "model:  local.sum" in out
    assert "skipped hosted.sum" in out


def test_explain_with_an_explicit_kind(routed, capsys):
    gateway, _ = routed
    assert (
        route_group.dispatch(
            parse(["explain", "Summarise this", "--kind", "chat"]), _gateway=gateway
        )
        == 0
    )
    assert "model:  local.chat" in capsys.readouterr().out


def test_an_unknown_kind_is_rejected_by_the_parser():
    with pytest.raises(SystemExit):
        parse(["explain", "x", "--kind", "poetry"])


def test_ask_sends_the_task_to_the_chosen_model(routed, capsys):
    gateway, provider = routed
    assert route_group.dispatch(parse(["ask", "Summarise this please"]), _gateway=gateway) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "a short summary"
    assert "summarise -> local.sum" in captured.err
    assert provider.prompts == [("local.sum", "Summarise this please")]


def test_a_routing_failure_is_a_clean_error(routed, tmp_path, monkeypatch, capsys):
    gateway, _ = routed
    table = tmp_path / "none.yaml"
    table.write_text("default: chat\ntasks:\n  chat: [not.registered]\n", encoding="utf-8")
    monkeypatch.setenv("WARNETECH_ROUTING_FILE", str(table))
    assert route_group.dispatch(parse(["ask", "hello"]), _gateway=gateway) == 1
    assert "error: no available model" in capsys.readouterr().err


def test_pipeline_command_prints_each_step(routed, tmp_path, capsys):
    gateway, _ = routed
    pipe = tmp_path / "p.yaml"
    pipe.write_text(
        "steps:\n  - {name: one, model: 'route:summarise', prompt: '{input}'}\n"
        "  - {name: two, model: 'route:chat', prompt: '{previous}'}\n",
        encoding="utf-8",
    )
    assert route_group.dispatch(parse(["pipeline", str(pipe), "some text"]), _gateway=gateway) == 0
    out = capsys.readouterr().out
    assert "--- one (local.sum)" in out and "--- two (local.chat)" in out
    assert "a short summary" in out and "hello back" in out


# ---- the shared error wrapper -------------------------------------------


def test_report_config_errors_turns_config_errors_into_exit_code_1(capsys):
    @report_config_errors
    def handler(_values):
        raise ConfigError("bad setup")

    assert handler({}) == 1
    assert capsys.readouterr().err == "error: bad setup\n"


def test_report_config_errors_does_not_hide_other_errors():
    @report_config_errors
    def handler(_values):
        raise RuntimeError("a real bug")

    with pytest.raises(RuntimeError):
        handler({})


def test_report_config_errors_passes_the_result_through():
    assert report_config_errors(lambda _v: 7)({}) == 7


def test_json_in_a_pipeline_prompt_survives_rendering():
    # The shipped extract step contains JSON braces next to {previous}.
    step = load_pipeline(PIPELINE_FILE).steps[1]
    text = render(step.prompt, original="o", previous="THE SUMMARY")
    assert '{"topic": string, "points": [string]}' in text and "THE SUMMARY" in text
