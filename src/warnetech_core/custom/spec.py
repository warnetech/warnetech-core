"""A custom model, described as data and validated before it becomes a Modelfile.

A spec names a base model (Llama, Mistral, Gemma, or any Ollama tag), a system
prompt, sampling parameters, and optionally a LoRA adapter trained elsewhere. The
checks here are what stand between a YAML file and the text handed to Ollama, so
they are strict: unknown parameters, out-of-range values, quote and newline
tricks, and adapter paths that leave the spec's directory are all rejected.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from ..errors import ConfigError
from ..runtime.ollama_cli import validate_model_name

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# "ollama" for this machine, or "ollama-<node>" for a node named in OLLAMA_NODES.
_PROVIDER_RE = re.compile(r"^ollama(-[a-z0-9][a-z0-9-]{0,31})?$")
MAX_SYSTEM_CHARS = 8000

# parameter -> (minimum, maximum, integer?)
PARAMETER_RULES: dict[str, tuple[float, float, bool]] = {
    "temperature": (0.0, 2.0, False),
    "top_p": (0.0, 1.0, False),
    "top_k": (1, 1000, True),
    "num_ctx": (256, 131_072, True),
    "repeat_penalty": (0.0, 3.0, False),
    "num_predict": (-2, 32_768, True),
    "seed": (0, 2**31 - 1, True),
}


class CustomModel(BaseModel):
    name: str = Field(description="Ollama model name to create, e.g. warnetech-general.")
    alias: str = Field(description="Registry alias the gateway will use, e.g. custom.general.")
    base: str = Field(description="Ollama tag to build on, e.g. llama3.2:3b.")
    provider: str = Field(
        default="ollama", description="Gateway provider that serves it: ollama or ollama-<node>."
    )
    description: str = ""
    system: str = ""
    parameters: dict[str, float | int] = Field(default_factory=dict)
    stop: list[str] = Field(default_factory=list)
    # Path to a LoRA adapter, relative to the spec file. Trained outside this repo.
    adapter: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not _NAME_RE.fullmatch(value):
            raise ValueError("must be lowercase letters, digits, '.', '_' or '-' (max 64)")
        return value

    @field_validator("alias")
    @classmethod
    def _check_alias(cls, value: str) -> str:
        if not _ALIAS_RE.fullmatch(value):
            raise ValueError("must be lowercase letters, digits, '.', '_' or '-' (max 64)")
        return value

    @field_validator("provider")
    @classmethod
    def _check_provider(cls, value: str) -> str:
        if not _PROVIDER_RE.fullmatch(value):
            raise ValueError("must be 'ollama' or 'ollama-<node>'")
        return value

    @field_validator("base")
    @classmethod
    def _check_base(cls, value: str) -> str:
        try:
            return validate_model_name(value)
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("description")
    @classmethod
    def _check_description(cls, value: str) -> str:
        # Rendered as a comment, so a newline would start a new Modelfile line.
        return " ".join(value.split())[:200]

    @field_validator("system")
    @classmethod
    def _check_system(cls, value: str) -> str:
        if len(value) > MAX_SYSTEM_CHARS:
            raise ValueError(f"longer than {MAX_SYSTEM_CHARS} characters")
        if '"""' in value:
            raise ValueError('must not contain """ (it would end the SYSTEM block)')
        if "\x00" in value:
            raise ValueError("must not contain NUL")
        return value.strip()

    @field_validator("parameters", mode="before")
    @classmethod
    def _reject_boolean_parameters(cls, value: Any) -> Any:
        # Runs before pydantic coerces types: afterwards `true` is already `1`, so a
        # YAML typo like `top_k: true` would pass as a valid number.
        if isinstance(value, dict):
            for key, raw in value.items():
                if isinstance(raw, bool):
                    raise ValueError(f"{key} must be a number, not a boolean")
        return value

    @field_validator("parameters")
    @classmethod
    def _check_parameters(cls, value: dict[str, float | int]) -> dict[str, float | int]:
        for key, raw in value.items():
            rule = PARAMETER_RULES.get(key)
            if rule is None:
                raise ValueError(f"unknown parameter {key!r}; allowed: {sorted(PARAMETER_RULES)}")
            low, high, integer = rule
            if integer and int(raw) != raw:
                raise ValueError(f"{key} must be a whole number")
            if not low <= raw <= high:
                raise ValueError(f"{key}={raw} is outside {low}..{high}")
        return value

    @field_validator("stop")
    @classmethod
    def _check_stop(cls, value: list[str]) -> list[str]:
        if len(value) > 8:
            raise ValueError("at most 8 stop strings")
        for item in value:
            if not item or len(item) > 64 or any(c in item for c in '"\n\r\x00'):
                raise ValueError(f"bad stop string {item!r}")
        return value

    @field_validator("adapter")
    @classmethod
    def _check_adapter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or "\x00" in value or "\n" in value:
            raise ValueError("must be a relative path inside the spec's directory")
        return value


def load_spec(path: str | Path) -> CustomModel:
    """Read and validate one spec file. Raises :class:`ConfigError` if it is bad."""
    file = Path(path)
    try:
        data: Any = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {file}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{file} must hold a mapping")
    try:
        return CustomModel(**data)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise ConfigError(f"{file.name}: {exc}") from exc


def load_specs(directory: str | Path) -> list[CustomModel]:
    """Load every ``*.yaml`` spec in a directory, rejecting duplicates."""
    root = Path(directory)
    if not root.is_dir():
        return []
    specs: list[CustomModel] = []
    names: set[str] = set()
    aliases: set[str] = set()
    for file in sorted(root.glob("*.yaml")):
        spec = load_spec(file)
        if spec.name in names or spec.alias in aliases:
            raise ConfigError(f"{file.name}: duplicate name or alias ({spec.name}, {spec.alias})")
        names.add(spec.name)
        aliases.add(spec.alias)
        specs.append(spec)
    return specs
