"""Configuration: a user file, an optional project file, and defaults that work before either exists.

Precedence, lowest to highest: built-in defaults < ``~/.aicli/config.toml`` <
the nearest ``.aicli.toml`` above the working directory (only once trusted).

API keys are never written to a config file. A provider names the environment
variable that holds its key (``api_key_env``), so a config is safe to commit.

A project config can start processes (MCP servers) and redirect providers, so a
freshly cloned repository's ``.aicli.toml`` is ignored until the user runs
``aicli project trust``. Trust is pinned to the file's content hash: when the
file changes, it must be trusted again.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_CONFIG = """\
# aicli configuration. Keys are read from the environment, never stored here.

# Local-first: the default model runs on your machine via Ollama.
default_model = "local/llama3.2"

# Tried in order when the requested model's provider fails. Empty means
# "never silently send my prompt somewhere else". Adding a cloud model here is
# how you opt in to graceful cloud fallback.
fallback = []

# true = refuse to send anything to a cloud provider, whatever is asked for.
local_only = false

format = "text"        # text | json | markdown
max_tokens = 1024

[providers.local]
type = "ollama"
base_url = "http://localhost:11434"

[providers.anthropic]
type = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"

[providers.openai]
type = "openai"
api_key_env = "OPENAI_API_KEY"
base_url = "https://api.openai.com/v1"

# Any OpenAI-compatible endpoint works the same way, e.g. DeepSeek:
# [providers.deepseek]
# type = "openai"
# api_key_env = "DEEPSEEK_API_KEY"
# base_url = "https://api.deepseek.com/v1"

# Offline echo provider: try aicli with no keys and no Ollama.
[providers.echo]
type = "fake"

[aliases]
opus = "anthropic/claude-opus-5"
sonnet = "anthropic/claude-sonnet-5"
haiku = "anthropic/claude-haiku-4-5"
gpt-4o = "openai/gpt-4o"
mistral = "local/mistral"

# --route cheapest|fastest|best picks from this pool. quality is your own
# 1-5 judgement of each model; --min-quality filters on it.
[routing]
pool = ["local/llama3.2", "haiku", "sonnet", "opus"]

[routing.quality]
"local/llama3.2" = 2
haiku = 3
sonnet = 4
opus = 5

# Spend ceilings in USD. A call whose worst-case estimate would cross one is
# refused (exit 3) unless --over-budget is passed. Remove a line for no limit.
[budget]
# daily_usd = 1.00
# monthly_usd = 20.00

# Override or add prices, USD per 1M tokens: model = [input, output]
[prices]

# MCP servers (stdio). Tools are denied by default: only names in `allow`
# are offered to the model. Use with: aicli "..." --mcp files
# [mcp.servers.files]
# command = "npx"
# args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
# allow = ["read_text_file", "list_directory"]
"""

VALID_FORMATS = ("text", "json", "markdown")
LOCAL_TYPES = ("ollama", "fake")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")
PROJECT_FILE = ".aicli.toml"
PROJECT_DIR = ".aicli"


class ConfigError(Exception):
    """The config file is missing something or contradicts itself."""


@dataclass
class ProviderConfig:
    name: str
    type: str
    base_url: str | None = None
    api_key_env: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    @property
    def is_local(self) -> bool:
        """True when a call cannot leave this machine."""
        if self.type in LOCAL_TYPES:
            host = urlparse(self.base_url).hostname if self.base_url else "localhost"
            return host in LOCAL_HOSTS
        return bool(self.base_url) and urlparse(self.base_url).hostname in LOCAL_HOSTS


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    allow: list[str] = field(default_factory=list)  # tool names, or ["*"]


@dataclass
class Config:
    default_model: str
    fallback: list[str]
    format: str
    max_tokens: int
    providers: dict[str, ProviderConfig]
    aliases: dict[str, str]
    prices: dict[str, tuple[float, float]]
    local_only: bool = False
    routing_pool: list[str] = field(default_factory=list)
    quality: dict[str, int] = field(default_factory=dict)
    daily_usd: float | None = None
    monthly_usd: float | None = None
    mcp: dict[str, MCPServerConfig] = field(default_factory=dict)
    path: Path | None = None
    project_root: Path | None = None


def home() -> Path:
    """Where config and history live. ``AICLI_HOME`` overrides, for tests and CI."""
    return Path(os.environ.get("AICLI_HOME") or Path.home() / ".aicli")


def config_path() -> Path:
    return home() / "config.toml"


def _loads(text: str, where: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{where}: {exc}") from exc


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Tables merge recursively; everything else in ``over`` replaces ``base``."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def build(raw: dict[str, Any], path: Path | None = None) -> Config:
    providers: dict[str, ProviderConfig] = {}
    for name, spec in raw.get("providers", {}).items():
        if "type" not in spec:
            raise ConfigError(f"provider {name!r} has no type")
        known = {"type", "base_url", "api_key_env"}
        providers[name] = ProviderConfig(
            name=name, type=spec["type"], base_url=spec.get("base_url"),
            api_key_env=spec.get("api_key_env"),
            options={k: v for k, v in spec.items() if k not in known},
        )

    fmt = raw.get("format", "text")
    if fmt not in VALID_FORMATS:
        raise ConfigError(f"format must be one of {VALID_FORMATS}, got {fmt!r}")

    prices = {}
    for model, pair in raw.get("prices", {}).items():
        if not (isinstance(pair, list) and len(pair) == 2):
            raise ConfigError(f"price for {model!r} must be [input, output]")
        prices[model] = (float(pair[0]), float(pair[1]))

    mcp = {}
    for name, spec in raw.get("mcp", {}).get("servers", {}).items():
        if "command" not in spec:
            raise ConfigError(f"mcp server {name!r} has no command")
        mcp[name] = MCPServerConfig(
            name=name, command=spec["command"], args=list(spec.get("args", [])),
            env=dict(spec.get("env", {})), allow=list(spec.get("allow", [])),
        )

    routing = raw.get("routing", {})
    budget = raw.get("budget", {})
    return Config(
        default_model=raw.get("default_model", "local/llama3.2"),
        fallback=list(raw.get("fallback", [])),
        format=fmt,
        max_tokens=int(raw.get("max_tokens", 1024)),
        providers=providers,
        aliases=dict(raw.get("aliases", {})),
        prices=prices,
        local_only=bool(raw.get("local_only", False)),
        routing_pool=list(routing.get("pool", [])),
        quality={k: int(v) for k, v in routing.get("quality", {}).items()},
        daily_usd=budget.get("daily_usd"),
        monthly_usd=budget.get("monthly_usd"),
        mcp=mcp,
        path=path,
    )


def parse(text: str, path: Path | None = None) -> Config:
    return build(_loads(text, str(path or "config")), path)


# --- project config ---------------------------------------------------------

def find_project(start: Path | None = None) -> Path | None:
    """The nearest directory at or above ``start`` holding a ``.aicli.toml``."""
    here = (start or Path.cwd()).resolve()
    stop = Path.home().resolve()
    for directory in (here, *here.parents):
        if (directory / PROJECT_FILE).is_file():
            return directory
        if directory == stop:
            break
    return None


def _trust_file() -> Path:
    return home() / "trusted.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_trusted(project: Path) -> bool:
    try:
        trusted = json.loads(_trust_file().read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return trusted.get(str(project)) == _digest(project / PROJECT_FILE)


def trust(project: Path) -> None:
    try:
        trusted = json.loads(_trust_file().read_text())
    except (OSError, json.JSONDecodeError):
        trusted = {}
    trusted[str(project)] = _digest(project / PROJECT_FILE)
    _trust_file().parent.mkdir(parents=True, exist_ok=True)
    _trust_file().write_text(json.dumps(trusted, indent=2))


def load(path: Path | None = None, cwd: Path | None = None, warn=None) -> Config:
    """User config (or defaults), with a trusted project config merged on top."""
    path = path or config_path()
    raw = _loads(path.read_text(encoding="utf-8"), str(path)) if path.exists() \
        else _loads(DEFAULT_CONFIG, "defaults")
    project = find_project(cwd)
    if project is not None:
        if is_trusted(project):
            proj_path = project / PROJECT_FILE
            raw = merge(raw, _loads(proj_path.read_text(encoding="utf-8"), str(proj_path)))
        elif warn:
            warn(f"note: {project / PROJECT_FILE} is not trusted, so it is ignored "
                 f"(review it, then run `aicli project trust`)")
    cfg = build(raw, path if path.exists() else None)
    cfg.project_root = project
    return cfg


def init(path: Path | None = None, force: bool = False) -> Path:
    path = path or config_path()
    if path.exists() and not force:
        raise ConfigError(f"{path} already exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return path


PROJECT_TEMPLATE = """\
# Project settings for aicli. Merged over ~/.aicli/config.toml once trusted.
# Commit this file; never put keys in it.

# default_model = "local/llama3.2"
# local_only = true

# [aliases]
# reviewer = "anthropic/claude-opus-5"
"""

EXAMPLE_TEMPLATE = """\
+++
# Optional front matter: defaults this template carries with it.
# model = "sonnet"
system = "You are a careful senior engineer. Be specific and brief."
+++
Review the following {{language}} change. List bugs first, then risks, then nits.

{{input}}
"""


def init_project(root: Path) -> list[Path]:
    """Create ``.aicli.toml`` and ``.aicli/templates`` in ``root``; trust what we wrote."""
    made = []
    cfg = root / PROJECT_FILE
    if not cfg.exists():
        cfg.write_text(PROJECT_TEMPLATE, encoding="utf-8")
        made.append(cfg)
    for sub in ("templates", "fragments"):
        (root / PROJECT_DIR / sub).mkdir(parents=True, exist_ok=True)
    example = root / PROJECT_DIR / "templates" / "review.md"
    if not example.exists():
        example.write_text(EXAMPLE_TEMPLATE, encoding="utf-8")
        made.append(example)
    trust(root)
    return made
