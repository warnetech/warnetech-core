import pytest

from aicli import config


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config and history; nothing touches ~/.aicli."""
    monkeypatch.setenv("AICLI_HOME", str(tmp_path / "aicli"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return tmp_path / "aicli"


@pytest.fixture
def write_config(isolated_home):
    def _write(text: str):
        path = config.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    return _write


ECHO_ONLY = """
default_model = "echo/echo"
[providers.echo]
type = "fake"
"""

FALLBACK = """
default_model = "broken/echo"
fallback = ["echo/echo"]
[providers.broken]
type = "fake"
fail = true
[providers.echo]
type = "fake"
"""
