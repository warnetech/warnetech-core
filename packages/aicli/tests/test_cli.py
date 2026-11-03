import json

from conftest import ECHO_ONLY, FALLBACK

from aicli import config
from aicli.cli import main


def test_bare_prompt_is_run_and_stdout_is_only_the_answer(write_config, capsys):
    write_config(ECHO_ONLY)
    assert main(["hello there"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == "echo: hello there"
    assert "est." in err and "✓ echo/echo" in err


def test_json_output_is_parseable(write_config, capsys):
    write_config(ECHO_ONLY)
    assert main(["hi", "--format", "json", "-q"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["text"] == "echo: hi"
    assert data["provider"] == "echo"
    assert data["cost_usd"] == 0.0
    assert data["fallback_from"] is None


def test_markdown_output_carries_metadata(write_config, capsys):
    write_config(ECHO_ONLY)
    main(["hi", "-f", "markdown", "-q"])
    out = capsys.readouterr().out
    assert out.startswith("echo: hi") and "echo/echo" in out


def test_fallback_is_announced_and_recorded(write_config, capsys):
    write_config(FALLBACK)
    assert main(["ping", "-f", "json"]) == 0
    out, err = capsys.readouterr()
    assert "broken/echo failed" in err
    assert json.loads(out)["fallback_from"] == "broken/echo"

    main(["history", "-f", "json"])
    rows = json.loads(capsys.readouterr().out)
    assert [r["status"] for r in rows] == ["ok", "error"]  # newest first
    assert rows[0]["fallback_from"] == "broken/echo"


def test_all_providers_failing_exits_1(write_config, capsys):
    write_config('default_model = "broken/x"\n[providers.broken]\ntype = "fake"\nfail = true\n')
    assert main(["ping"]) == 1
    assert "every candidate failed" in capsys.readouterr().err


def test_unknown_model_is_a_config_error_not_a_crash(write_config, capsys):
    write_config(ECHO_ONLY)
    assert main(["hi", "-m", "mystery-model"]) == 2
    assert "cannot tell which provider" in capsys.readouterr().err


def test_no_save_keeps_history_empty(write_config, capsys):
    write_config(ECHO_ONLY)
    main(["secret", "--no-save", "-q"])
    capsys.readouterr()
    main(["history", "-f", "json"])
    assert json.loads(capsys.readouterr().out) == []


def test_search_finds_past_responses(write_config, capsys):
    write_config(ECHO_ONLY)
    main(["quantum computing", "-q"])
    main(["sourdough bread", "-q"])
    capsys.readouterr()
    main(["search", "quantum", "-f", "json"])
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1 and rows[0]["prompt"] == "quantum computing"


def test_search_tolerates_fts_punctuation(write_config, capsys):
    write_config(ECHO_ONLY)
    main(['what is "AND" OR (x)?', "-q"])
    capsys.readouterr()
    assert main(["search", '"AND"', "(x)?"]) == 0


def test_stats_aggregates_per_model(write_config, capsys):
    write_config(ECHO_ONLY)
    main(["a", "-q"])
    main(["b", "-q"])
    capsys.readouterr()
    main(["stats", "-f", "json"])
    (row,) = json.loads(capsys.readouterr().out)
    assert row["calls"] == 2 and row["errors"] == 0 and row["cost_usd"] == 0


def test_chat_keeps_context_and_quits(write_config, capsys, monkeypatch):
    write_config(ECHO_ONLY)
    lines = iter(["first", "second", ":quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))
    assert main(["chat", "-q"]) == 0
    out = capsys.readouterr().out
    assert "echo> echo: first" in out and "echo> echo: second" in out


def test_list_providers_reports_missing_keys_without_raising(capsys):
    assert main(["list", "providers", "-f", "json"]) == 0  # built-in defaults
    rows = {r["provider"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["anthropic"]["available"] is False
    assert "ANTHROPIC_API_KEY" in rows["anthropic"]["detail"]
    assert rows["echo"]["available"] is True


def test_config_init_writes_defaults_once(capsys):
    assert main(["config", "init"]) == 0
    assert config.config_path().read_text() == config.DEFAULT_CONFIG
    assert main(["config", "init"]) == 2
    assert main(["config", "init", "--force"]) == 0
