"""Agent definitions: small YAML files, validated before use."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from ..errors import ConfigError

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MAX_SYSTEM_CHARS = 4000


class AgentSpec(BaseModel):
    name: str
    description: str = ""
    model: str = "chat.default"
    system: str = Field(default="", description="Instructions for this agent.")
    max_tokens: int = Field(default=700, ge=1, le=4096)
    temperature: float = Field(default=0.3, ge=0.0, le=1.5)
    # Whether the agent may read your memory (retrieval only; it never writes).
    memory: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME_RE.fullmatch(value):
            raise ValueError("must be lowercase letters, digits or '-' (max 32)")
        return value

    @field_validator("system")
    @classmethod
    def _system(cls, value: str) -> str:
        if len(value) > MAX_SYSTEM_CHARS:
            raise ValueError(f"longer than {MAX_SYSTEM_CHARS} characters")
        return value.strip()

    @field_validator("memory", mode="before")
    @classmethod
    def _memory_is_bool(cls, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
        return value


def load_agents(directory: str | Path = "config/agents") -> dict[str, AgentSpec]:
    """Load every ``*.yaml`` in a directory. Raises :class:`ConfigError` if one is bad."""
    root = Path(directory)
    if not root.is_dir():
        return {}
    agents: dict[str, AgentSpec] = {}
    for file in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"cannot read {file}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{file.name} must hold a mapping")
        try:
            spec = AgentSpec(**data)
        except ValueError as exc:
            raise ConfigError(f"{file.name}: {exc}") from exc
        if spec.name in agents:
            raise ConfigError(f"{file.name}: duplicate agent name {spec.name!r}")
        agents[spec.name] = spec
    return agents
