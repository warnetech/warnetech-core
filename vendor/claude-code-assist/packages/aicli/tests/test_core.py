import json

import pytest

from aicli import config, pricing, providers
from aicli.config import ConfigError, ProviderConfig
from aicli.router import Router, Target


def test_default_config_parses_and_is_local_first():
    cfg = config.parse(config.DEFAULT_CONFIG)
    router = Router(cfg)
    assert router.resolve(None) == Target("local", "llama3.2")
    assert cfg.fallback == []  # never silently ship a prompt to the cloud


def test_resolution_rules():
    router = Router(config.parse(config.DEFAULT_CONFIG))
    assert router.resolve("sonnet") == Target("anthropic", "claude-sonnet-5")
    assert router.resolve("local/mistral") == Target("local", "mistral")
    assert router.resolve("claude-opus-5") == Target("anthropic", "claude-opus-5")
    assert router.resolve("gpt-4o-mini") == Target("openai", "gpt-4o-mini")
    # Model names containing slashes survive when the head is a provider.
    assert router.resolve("local/hf.co/org/model") == Target("local", "hf.co/org/model")


def test_bad_config_is_reported_clearly():
    with pytest.raises(ConfigError, match="no type"):
        config.parse("[providers.x]\nbase_url='y'\n")
    with pytest.raises(ConfigError, match="format"):
        config.parse("format = 'yaml'\n")
    with pytest.raises(ConfigError, match="unknown type"):
        providers.build(ProviderConfig(name="x", type="nope"))


def test_pricing():
    assert pricing.price_of("claude-opus-5", "anthropic") == (5.0, 25.0)
    assert pricing.price_of("anything", "ollama") == (0.0, 0.0)
    assert pricing.price_of("mystery", "openai") is None
    assert pricing.price_of("mystery", "openai", {"mystery": (1.0, 2.0)}) == (1.0, 2.0)
    # gpt-4o-mini must not be priced as gpt-4o (longest prefix wins).
    assert pricing.price_of("gpt-4o-mini-2024", "openai") == (0.15, 0.60)
    assert pricing.cost((3.0, 15.0), 1_000_000, 1_000_000) == 18.0
    assert pricing.cost(None, 10, 10) is None
    assert pricing.fmt_usd(None).startswith("n/a")


class FakeHTTP:
    """Captures the request a provider builds and returns a canned body."""

    def __init__(self, response):
        self.response, self.calls = response, []

    def __call__(self, method, url, body=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_anthropic_request_shape_and_parsing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    http = FakeHTTP({"model": "claude-opus-5", "stop_reason": "end_turn",
                     "content": [{"type": "text", "text": "hi"}],
                     "usage": {"input_tokens": 3, "output_tokens": 1}})
    monkeypatch.setattr(providers, "_http", http)
    p = providers.build(ProviderConfig("anthropic", "anthropic", api_key_env="ANTHROPIC_API_KEY"))
    c = p.complete([{"role": "user", "content": "yo"}], "claude-opus-5", 50, system="be brief")
    assert (c.text, c.input_tokens, c.output_tokens) == ("hi", 3, 1)
    call = http.calls[0]
    assert call["url"].endswith("/v1/messages")
    assert call["headers"]["x-api-key"] == "sk-test"
    assert call["body"]["system"] == "be brief" and call["body"]["max_tokens"] == 50


def test_anthropic_refusal_is_an_error_not_an_empty_answer(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(providers, "_http", FakeHTTP({"stop_reason": "refusal", "content": []}))
    p = providers.build(ProviderConfig("a", "anthropic", api_key_env="ANTHROPIC_API_KEY"))
    with pytest.raises(providers.ProviderError, match="refused"):
        p.complete([{"role": "user", "content": "x"}], "claude-opus-5", 10)


def test_missing_key_fails_before_any_network_call(monkeypatch):
    http = FakeHTTP({})
    monkeypatch.setattr(providers, "_http", http)
    p = providers.build(ProviderConfig("o", "openai", api_key_env="OPENAI_API_KEY"))
    with pytest.raises(providers.ProviderError, match="OPENAI_API_KEY"):
        p.complete([{"role": "user", "content": "x"}], "gpt-4o", 10)
    assert http.calls == []


def test_openai_and_ollama_parsing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(providers, "_http", FakeHTTP({
        "model": "gpt-4o", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2}}))
    o = providers.build(ProviderConfig("openai", "openai", api_key_env="OPENAI_API_KEY"))
    c = o.complete([{"role": "user", "content": "x"}], "gpt-4o", 10)
    assert (c.text, c.input_tokens, c.output_tokens) == ("ok", 5, 2)

    monkeypatch.setattr(providers, "_http", FakeHTTP({
        "model": "llama3.2", "message": {"content": "local"}, "done_reason": "stop",
        "prompt_eval_count": 4, "eval_count": 9}))
    lo = providers.build(ProviderConfig("local", "ollama"))
    c = lo.complete([{"role": "user", "content": "x"}], "llama3.2", 10)
    assert (c.text, c.input_tokens, c.output_tokens) == ("local", 4, 9)


def test_ollama_down_is_unavailable_not_an_exception(monkeypatch):
    monkeypatch.setattr(providers, "_http", FakeHTTP(providers.ProviderError("refused")))
    ok, why = providers.build(ProviderConfig("local", "ollama")).available()
    assert ok is False and "not reachable" in why


def test_real_http_errors_become_provider_errors():
    # Port 9 (discard) on localhost: nothing listens, so this fails fast offline.
    with pytest.raises(providers.ProviderError, match="cannot reach"):
        providers._http("GET", "http://127.0.0.1:9/", timeout=1)


def test_history_db_is_plain_sqlite(write_config, capsys):
    import sqlite3

    from aicli.cli import main

    write_config('default_model = "echo/echo"\n[providers.echo]\ntype = "fake"\n')
    main(["hi", "-q"])
    db = sqlite3.connect(config.home() / "history.db")
    assert db.execute("SELECT prompt, response FROM runs").fetchall() == [("hi", "echo: hi")]
    json.dumps(db.execute("SELECT * FROM runs").fetchall())  # exportable as-is
