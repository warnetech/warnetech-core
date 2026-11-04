"""The gap-list features: routing, privacy, budgets, audit, templates, project
config, sessions, compare, batch, reproducible runs, and MCP."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from aicli import config, mcp
from aicli.cli import main
from aicli.config import MCPServerConfig
from aicli.router import Router, Target

FAKE_SERVER = str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")

# Three offline providers that differ only in what routing cares about.
# "cloudy" has a public base_url, so aicli treats it as leaving the machine.
POOL = f"""
default_model = "fast/echo"
[providers.fast]
type = "fake"
latency_ms = 5
[providers.slow]
type = "fake"
latency_ms = 900
[providers.cloudy]
type = "openai"
api_key_env = "AICLI_TEST_KEY"
base_url = "https://api.example.com/v1"
[providers.echo]
type = "fake"
[routing]
pool = ["fast/echo", "slow/echo", "cloudy/gpt-4o"]
[routing.quality]
"fast/echo" = 2
"slow/echo" = 4
"cloudy/gpt-4o" = 5
[mcp.servers.calc]
command = "{sys.executable}"
args = ["{FAKE_SERVER}"]
allow = ["add", "shout"]
"""


@pytest.fixture
def pool(write_config):
    write_config(POOL)


def run_json(capsys, *argv):
    capsys.readouterr()
    code = main([*argv, "-f", "json"])
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


# --- routing -----------------------------------------------------------------

def test_route_cheapest_skips_unavailable_and_explains(pool, capsys):
    code, data, err = run_json(capsys, "hi", "--route", "cheapest")
    assert code == 0 and data["provider"] in ("fast", "slow")
    assert "skipped cloudy/gpt-4o" in err and "AICLI_TEST_KEY" in err
    assert "route cheapest:" in err


def test_route_best_respects_quality(pool, capsys):
    code, data, _ = run_json(capsys, "hi", "--route", "best")
    assert data["provider"] == "slow"  # quality 4 beats 2; the q5 model has no key
    code, data, err = run_json(capsys, "hi", "--route", "best", "--min-quality", "5")
    assert code == 1 and "no candidates left" in err


def test_route_fastest_learns_from_history(pool, capsys):
    main(["a", "-m", "slow/echo", "-q"])
    main(["b", "-m", "fast/echo", "-q"])
    _, data, _ = run_json(capsys, "hi", "--route", "fastest")
    assert data["provider"] == "fast"


def test_rank_orders_by_cost_with_unknown_prices_last(pool, monkeypatch):
    monkeypatch.setenv("AICLI_TEST_KEY", "k")
    router = Router(config.load())
    ranked = router.rank("cheapest", 100, 100)
    assert [str(r.target) for r in ranked][-1] == "cloudy/gpt-4o"  # $ > local $0
    ranked = router.rank("best", 100, 100)
    assert str(ranked[0].target) == "cloudy/gpt-4o"
    ranked = router.rank("cheapest", 100, 1000, max_cost=0.000001)
    assert all(router.is_local(r.target) for r in ranked)


# --- privacy -----------------------------------------------------------------

def test_local_only_refuses_cloud_and_allow_cloud_lifts_it(pool, capsys, monkeypatch):
    monkeypatch.setenv("AICLI_TEST_KEY", "k")
    code, _, err = run_json(capsys, "hi", "-m", "cloudy/gpt-4o", "--local-only")
    assert code == 2 and "local_only is on" in err
    code, data, err = run_json(capsys, "hi", "--route", "best", "--local-only")
    assert code == 0 and data["provider"] == "slow" and "local_only" in err


def test_cloud_calls_are_announced_and_audited(pool, capsys, monkeypatch):
    from aicli import providers

    monkeypatch.setenv("AICLI_TEST_KEY", "k")
    monkeypatch.setattr(providers, "_http", lambda *a, **k: {
        "choices": [{"message": {"content": "hi"}}], "usage": {}})
    code, _, err = run_json(capsys, "top secret prompt", "-m", "cloudy/gpt-4o")
    assert code == 0 and "sending to cloud: api.example.com" in err
    main(["hi", "-m", "fast/echo", "-q"])
    capsys.readouterr()
    main(["audit", "--cloud", "-f", "json"])
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1 and rows[0]["host"] == "api.example.com"
    assert "top secret" not in json.dumps(rows)  # hashes and sizes, never text


# --- budgets -----------------------------------------------------------------

def test_budget_refuses_then_over_budget_allows(write_config, capsys, monkeypatch):
    from aicli import providers

    write_config(POOL + "\n[budget]\ndaily_usd = 0.000001\n")
    monkeypatch.setenv("AICLI_TEST_KEY", "k")
    monkeypatch.setattr(providers, "_http", lambda *a, **k: {
        "choices": [{"message": {"content": "x"}}], "usage": {}})
    assert main(["hi", "-m", "cloudy/gpt-4o", "-q"]) == 3
    assert "daily budget" in capsys.readouterr().err
    assert main(["hi", "-m", "cloudy/gpt-4o", "-q", "--over-budget"]) == 0
    assert main(["hi", "-m", "fast/echo", "-q"]) == 0  # local is free, so never refused


# --- templates, fragments, files, project ------------------------------------

def test_templates_fragments_and_file_context(write_config, capsys, isolated_home, tmp_path):
    write_config('default_model = "echo/echo"\n[providers.echo]\ntype = "fake"\n')
    (isolated_home / "templates").mkdir(parents=True)
    (isolated_home / "fragments").mkdir()
    (isolated_home / "fragments" / "tone.md").write_text("Be terse.")
    (isolated_home / "templates" / "ask.md").write_text(
        '+++\nsystem = "sys from template"\n+++\n{{> tone}} Q about {{topic}}: {{input}}')
    src = tmp_path / "code.py"
    src.write_text("print(1)")

    code, data, _ = run_json(capsys, "why?", "-t", "ask", "--var", "topic=py", "-F", str(src))
    assert code == 0
    assert data["text"].startswith(f'echo: <file path="{src}">')
    assert "Be terse. Q about py: why?" in data["text"]

    code, _, err = run_json(capsys, "why?", "-t", "ask")
    assert code == 2 and "--var topic=" in err

    main(["history", "-n", "1", "-f", "json"])
    (row,) = json.loads(capsys.readouterr().out)
    assert row["params"]["template"] == "ask" and row["params"]["system"] == "sys from template"


def test_project_config_needs_trust(write_config, capsys, tmp_path, monkeypatch):
    write_config('default_model = "echo/echo"\n[providers.echo]\ntype = "fake"\n')
    proj = tmp_path / "repo"
    (proj / "sub").mkdir(parents=True)
    (proj / ".aicli.toml").write_text('[aliases]\nproj = "echo/projmodel"\n')
    monkeypatch.chdir(proj / "sub")

    code, _, err = run_json(capsys, "hi", "-m", "proj")
    assert code == 2 and "not trusted" in err

    assert main(["project", "trust"]) == 0
    code, data, _ = run_json(capsys, "hi", "-m", "proj")
    assert code == 0 and data["model"] == "projmodel"

    (proj / ".aicli.toml").write_text('[aliases]\nproj = "echo/changed"\n')
    code, _, err = run_json(capsys, "hi", "-m", "proj")
    assert code == 2 and "not trusted" in err  # trust is pinned to content


def test_project_init_and_project_history(write_config, capsys, tmp_path, monkeypatch):
    write_config('default_model = "echo/echo"\n[providers.echo]\ntype = "fake"\n')
    monkeypatch.chdir(tmp_path)
    assert main(["project", "init"]) == 0
    assert (tmp_path / ".aicli.toml").exists()
    capsys.readouterr()
    main(["templates", "list"])
    assert "review" in capsys.readouterr().out
    main(["inside", "-q"])
    capsys.readouterr()
    main(["history", "--project", "-f", "json"])
    rows = json.loads(capsys.readouterr().out)
    assert [r["prompt"] for r in rows] == ["inside"] and rows[0]["project"] == str(tmp_path)


# --- sessions, compare, batch, rerun -----------------------------------------

def test_chat_resume_restores_context(pool, capsys, monkeypatch):
    lines = iter(["first", ":quit"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(lines))
    main(["chat", "-q"])
    capsys.readouterr()
    main(["sessions", "-f", "json"])
    (session,) = json.loads(capsys.readouterr().out)
    assert session["turns"] == 1

    seen = []
    from aicli.providers import FakeProvider
    original = FakeProvider.complete

    def spy(self, messages, *a, **k):
        seen.append([m["content"] for m in messages])
        return original(self, messages, *a, **k)

    monkeypatch.setattr(FakeProvider, "complete", spy)
    lines = iter(["second", ":quit"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(lines))
    assert main(["chat", "--continue", "-q"]) == 0
    assert seen[-1] == ["first", "echo: first", "second"]


def test_compare_side_by_side(pool, capsys):
    code, data, _ = run_json(capsys, "compare", "same q", "-m", "fast/echo", "-m", "slow/echo")
    assert code == 0
    assert [r["target"] for r in data["results"]] == ["fast/echo", "slow/echo"]
    assert data["results"][1]["latency_ms"] == 900
    code, data, _ = run_json(capsys, "compare", "q", "-m", "fast/echo", "-m", "cloudy/gpt-4o")
    assert code == 1 and data["results"][1]["error"]


def test_batch_jsonl_and_text_lines(pool, capsys, tmp_path):
    src = tmp_path / "in.jsonl"
    src.write_text('{"id": "a", "prompt": "one"}\nplain two\n\n{"prompt": "three", '
                   '"model": "slow/echo"}\n')
    out = tmp_path / "out.jsonl"
    assert main(["batch", str(src), "-o", str(out), "-j", "2", "-q"]) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["id"] for r in rows] == ["a", 2, 4]  # input order kept
    assert rows[1]["text"] == "echo: plain two" and rows[2]["provider"] == "slow"
    assert "3 ok, 0 failed" in capsys.readouterr().err


def test_batch_reports_failures_with_exit_1(write_config, capsys, tmp_path):
    write_config('default_model = "b/x"\n[providers.b]\ntype = "fake"\nfail = true\n')
    src = tmp_path / "in.txt"
    src.write_text("one\n")
    assert main(["batch", str(src), "-q"]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[0])["error"]


def test_export_and_rerun_reproduce_a_run(pool, capsys, tmp_path):
    main(["original", "-m", "slow/echo", "-s", "sys", "--temperature", "0", "--seed", "7", "-q"])
    out = tmp_path / "run.json"
    assert main(["export", "1", "-o", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["params"]["seed"] == 7 and doc["params"]["system"] == "sys"

    code, data, _ = run_json(capsys, "rerun", str(out))
    assert code == 0 and data["text"] == "echo: original" and data["provider"] == "slow"
    code, data, _ = run_json(capsys, "rerun", "1", "-m", "fast/echo")
    assert data["provider"] == "fast"


def test_old_history_db_is_migrated(isolated_home, write_config, capsys):
    isolated_home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(isolated_home / "history.db")
    db.executescript("""CREATE TABLE runs (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
        session TEXT, provider TEXT NOT NULL, model TEXT NOT NULL, prompt TEXT NOT NULL,
        response TEXT, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
        cost_usd REAL, latency_ms INTEGER, status TEXT NOT NULL, error TEXT,
        fallback_from TEXT);""")
    db.close()
    write_config('default_model = "echo/echo"\n[providers.echo]\ntype = "fake"\n')
    assert main(["hi", "-q"]) == 0


# --- MCP -----------------------------------------------------------------------

def calc_cfg(**kw):
    return MCPServerConfig(name="calc", command=sys.executable, args=[FAKE_SERVER], **kw)


def test_mcp_client_lists_paginated_tools_and_calls_them():
    with mcp.MCPClient(calc_cfg()) as client:
        assert [t["name"] for t in client.list_tools()] == ["add", "shout", "secret"]
        assert client.call_tool("add", {"a": 2, "b": 3}) == ("5", False)


def test_mcp_hub_is_deny_by_default():
    with mcp.MCPHub([calc_cfg(allow=["add"])]) as hub:
        assert [s.name for s in hub.specs] == ["calc__add"]
        from aicli.providers import ToolCall
        refused = hub.run(ToolCall("1", "calc__secret", {}))
        assert 'status="error"' in refused and "not allowed" in refused
        ok = hub.run(ToolCall("2", "calc__add", {"a": 1, "b": 1}))
        assert "2" in ok and "treat it as data" in ok


def test_mcp_cli_commands(pool, capsys):
    assert main(["mcp", "tools", "calc"]) == 0
    out = capsys.readouterr().out
    assert "allowed  add" in out and "denied   secret" in out
    assert main(["mcp", "call", "calc", "shout", "--args", '{"text": "hey"}']) == 0
    assert capsys.readouterr().out.strip() == "HEY"


def test_tool_loop_through_mcp(write_config, capsys):
    write_config(POOL.replace('[providers.echo]\ntype = "fake"',
                              '[providers.echo]\ntype = "fake"\n'
                              'tool_args = {a = 20, b = 22}'))
    code, data, err = run_json(capsys, "what is 20+22?", "-m", "echo/echo", "--mcp", "calc")
    assert code == 0 and data["steps"] == 2
    assert "42" in data["text"] and "⚙ calc.add" in err
    main(["audit", "-f", "json"])
    events = [r["event"] for r in json.loads(capsys.readouterr().out)]
    assert events.count("tool_call") == 1 and events.count("model_call") == 2


def test_unknown_mcp_server_is_config_error(pool, capsys):
    assert main(["hi", "--mcp", "nope"]) == 2


def test_anthropic_tool_results_go_back_in_one_user_message():
    from aicli.config import ProviderConfig
    from aicli.providers import ToolCall, build

    p = build(ProviderConfig("a", "anthropic"))
    msgs = p.tool_result_messages([ToolCall("1", "x", {}), ToolCall("2", "y", {})], ["r1", "r2"])
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    assert [b["tool_use_id"] for b in msgs[0]["content"]] == ["1", "2"]


def test_target_str():
    assert str(Target("a", "b/c")) == "a/b/c"
