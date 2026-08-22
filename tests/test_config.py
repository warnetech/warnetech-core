import pytest

from warnetech_core.config import Settings, _parse_budgets, build
from warnetech_core.memory.inmemory import InMemoryContextStore
from warnetech_core.memory.sqlite import SQLiteContextStore
from warnetech_core.usage import InMemoryUsageStore, SQLiteUsageStore


@pytest.fixture(autouse=True)
def offline_env(monkeypatch):
    monkeypatch.setenv("WARNETECH_OFFLINE", "1")
    monkeypatch.setenv("WARNETECH_MEMORY_BACKEND", "memory")
    monkeypatch.setenv("WARNETECH_USAGE_BACKEND", "memory")


def test_budget_strings_parse_into_a_map():
    assert _parse_budgets("a=1.5, b=10") == {"a": 1.5, "b": 10.0}
    assert _parse_budgets("") == {}
    assert _parse_budgets(None) == {}


def test_malformed_budget_string_is_rejected():
    with pytest.raises(ValueError, match="name=amount"):
        _parse_budgets("just-a-name")


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("ON", True),
                                          ("0", False), ("no", False), ("", False)])
def test_boolean_env_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("WARNETECH_ENABLE_FALLBACK", raw)
    assert Settings.from_env().enable_fallback is expected


def test_environment_populates_settings(monkeypatch):
    monkeypatch.setenv("WARNETECH_PROJECT", "hub")
    monkeypatch.setenv("WARNETECH_TEAM", "core")
    monkeypatch.setenv("WARNETECH_PROJECT_BUDGETS", "hub=2.50")
    settings = Settings.from_env()
    assert (settings.project, settings.team) == ("hub", "core")
    assert settings.project_budgets_usd == {"hub": 2.50}


def test_keyword_overrides_beat_the_environment(monkeypatch):
    monkeypatch.setenv("WARNETECH_PROJECT", "from-env")
    assert Settings.from_env(project="explicit").project == "explicit"


def test_offline_settings_repoint_the_registry():
    assert Settings.from_env().build_registry().resolve("chat.default").provider == "echo"


def test_model_file_overrides_win_over_offline_defaults(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text("aliases:\n  chat.default: openai.gpt-4o\n")
    settings = Settings.from_env(models_file=str(path))
    assert settings.build_registry().resolve("chat.default").provider == "openai"


@pytest.mark.parametrize("backend,expected", [("memory", InMemoryContextStore),
                                              ("sqlite", SQLiteContextStore)])
def test_memory_backend_selection(tmp_path, backend, expected):
    settings = Settings.from_env(
        memory_backend=backend, memory_path=str(tmp_path / "m.db")
    )
    assert isinstance(settings.build_store(), expected)


@pytest.mark.parametrize("backend,expected", [("memory", InMemoryUsageStore),
                                              ("sqlite", SQLiteUsageStore)])
def test_usage_backend_selection(tmp_path, backend, expected):
    settings = Settings.from_env(usage_backend=backend, usage_path=str(tmp_path / "u.db"))
    assert isinstance(settings.build_usage().store, expected)


def test_unknown_backends_are_rejected():
    with pytest.raises(ValueError, match="unknown memory backend"):
        Settings.from_env(memory_backend="mystery").build_store()
    with pytest.raises(ValueError, match="unknown usage backend"):
        Settings.from_env(usage_backend="mystery").build_usage()


def test_pgvector_without_a_dsn_says_so():
    with pytest.raises(ValueError, match="WARNETECH_POSTGRES_DSN"):
        Settings.from_env(memory_backend="pgvector", postgres_dsn=None).build_store()


async def test_build_returns_a_working_pair():
    gateway, memory = build(project="hub")
    try:
        await memory.remember("hub", "a fact worth keeping", key="f")
        result = await memory.get_context("hub", "a fact")
        assert "worth keeping" in result.text
        response = await gateway.complete("chat.default", "hi", project="hub")
        assert response.text == "echo:hi"
        assert gateway.usage.summary(group_by="project")[0].key == "hub"
    finally:
        await memory.aclose()
        await gateway.aclose()


def test_budgets_reach_the_tracker():
    settings = Settings.from_env(project_budgets_usd={"hub": 1.0})
    assert settings.build_gateway().usage.project_budgets == {"hub": 1.0}
