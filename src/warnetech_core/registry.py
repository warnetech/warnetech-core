"""Model registry: logical aliases -> concrete provider models.

Downstream code asks for ``"chat.default"`` or ``"embed.default"``. Which
provider and which model id that resolves to — and what it costs — lives here
and nowhere else. Swapping a provider is an edit to this file (or to the YAML
overrides loaded at startup), never to a calling project.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import yaml

from .errors import ConfigError, ModelNotRegistered

Modality = Literal["chat", "embedding"]


@dataclass(frozen=True)
class ModelSpec:
    """One concrete, callable model plus everything we need to bill it."""

    alias: str
    provider: str
    provider_model: str
    modality: Modality = "chat"
    # USD per 1M tokens. Embedding models price output at 0.
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    context_window: int = 200_000
    max_output_tokens: int = 8192
    dimensions: int | None = None
    # Aliases tried, in order, when this model fails hard.
    fallbacks: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        """Cost of a call at this model's rates."""
        return (
            input_tokens * self.input_cost_per_mtok
            + output_tokens * self.output_cost_per_mtok
        ) / 1_000_000


# Built-in catalogue. Prices are USD per 1M tokens and WILL drift — override
# them via `models.yaml` rather than editing this table in a hurry.
_BUILTIN: tuple[ModelSpec, ...] = (
    ModelSpec(
        alias="anthropic.claude-sonnet-4-5",
        provider="anthropic",
        provider_model="claude-sonnet-4-5",
        input_cost_per_mtok=3.0,
        output_cost_per_mtok=15.0,
        context_window=200_000,
        max_output_tokens=64_000,
        tags=("balanced",),
    ),
    ModelSpec(
        alias="anthropic.claude-haiku-4-5",
        provider="anthropic",
        provider_model="claude-haiku-4-5",
        input_cost_per_mtok=1.0,
        output_cost_per_mtok=5.0,
        context_window=200_000,
        max_output_tokens=64_000,
        tags=("fast", "cheap"),
    ),
    ModelSpec(
        alias="openai.gpt-4o",
        provider="openai",
        provider_model="gpt-4o",
        input_cost_per_mtok=2.5,
        output_cost_per_mtok=10.0,
        context_window=128_000,
        max_output_tokens=16_384,
        tags=("balanced",),
    ),
    ModelSpec(
        alias="openai.gpt-4o-mini",
        provider="openai",
        provider_model="gpt-4o-mini",
        input_cost_per_mtok=0.15,
        output_cost_per_mtok=0.6,
        context_window=128_000,
        max_output_tokens=16_384,
        tags=("fast", "cheap"),
    ),
    ModelSpec(
        alias="openai.text-embedding-3-small",
        provider="openai",
        provider_model="text-embedding-3-small",
        modality="embedding",
        input_cost_per_mtok=0.02,
        context_window=8_191,
        dimensions=1536,
    ),
    ModelSpec(
        alias="openai.text-embedding-3-large",
        provider="openai",
        provider_model="text-embedding-3-large",
        modality="embedding",
        input_cost_per_mtok=0.13,
        context_window=8_191,
        dimensions=3072,
    ),
    # Offline/deterministic models — used by tests and by local development so a
    # mini-project can run end-to-end with no API keys at all.
    ModelSpec(
        alias="echo.chat",
        provider="echo",
        provider_model="echo-chat",
        tags=("offline",),
    ),
    ModelSpec(
        alias="hash.embed",
        provider="echo",
        provider_model="hash-embed",
        modality="embedding",
        dimensions=256,
        tags=("offline",),
    ),
)

# Task-level aliases. This indirection is the whole point of the registry:
# projects call `chat.default`, and swapping the underlying model is a one-line
# change here.
_DEFAULT_ALIASES: dict[str, str] = {
    "chat.default": "anthropic.claude-sonnet-4-5",
    "chat.fast": "anthropic.claude-haiku-4-5",
    "chat.cheap": "openai.gpt-4o-mini",
    "embed.default": "openai.text-embedding-3-small",
    "embed.large": "openai.text-embedding-3-large",
}

# Aliases applied when running offline, so every task alias resolves to a
# deterministic local model. Lives here (not in the gateway) so that anything
# reporting on the registry — the CLI, the /models endpoint — agrees with what
# the gateway will actually call.
OFFLINE_ALIASES: dict[str, str] = {
    "chat.default": "echo.chat",
    "chat.fast": "echo.chat",
    "chat.cheap": "echo.chat",
    "embed.default": "hash.embed",
    "embed.large": "hash.embed",
}

_DEFAULT_FALLBACKS: dict[str, tuple[str, ...]] = {
    "anthropic.claude-sonnet-4-5": ("openai.gpt-4o",),
    "anthropic.claude-haiku-4-5": ("openai.gpt-4o-mini",),
    "openai.gpt-4o": ("anthropic.claude-sonnet-4-5",),
    "openai.gpt-4o-mini": ("anthropic.claude-haiku-4-5",),
}


class ModelRegistry:
    """Resolves aliases to :class:`ModelSpec`s, with fallback chains."""

    def __init__(
        self,
        specs: list[ModelSpec] | None = None,
        aliases: dict[str, str] | None = None,
    ) -> None:
        self._specs: dict[str, ModelSpec] = {}
        self._aliases: dict[str, str] = dict(aliases or {})
        for spec in specs if specs is not None else _BUILTIN:
            self.register(spec)

    @classmethod
    def default(cls) -> ModelRegistry:
        """Registry with the built-in catalogue, task aliases and fallbacks."""
        specs = [
            replace(s, fallbacks=_DEFAULT_FALLBACKS.get(s.alias, s.fallbacks)) for s in _BUILTIN
        ]
        return cls(specs=specs, aliases=dict(_DEFAULT_ALIASES))

    def register(self, spec: ModelSpec) -> None:
        """Add or replace a model spec."""
        self._specs[spec.alias] = spec

    def alias(self, name: str, target: str) -> None:
        """Point a task alias (``chat.default``) at a concrete model alias."""
        if target not in self._specs and target not in self._aliases:
            raise ModelNotRegistered(f"cannot alias {name!r} -> unknown model {target!r}")
        self._aliases[name] = target

    def resolve(self, name: str) -> ModelSpec:
        """Resolve a task alias or model alias to a concrete spec."""
        seen: set[str] = set()
        current = name
        while current in self._aliases:
            if current in seen:
                raise ConfigError(f"alias cycle while resolving {name!r}")
            seen.add(current)
            current = self._aliases[current]
        try:
            return self._specs[current]
        except KeyError:
            raise ModelNotRegistered(
                f"model {name!r} is not registered "
                f"(known: {', '.join(sorted(self.known_names()))})"
            ) from None

    def chain(self, name: str, *, max_length: int = 4) -> list[ModelSpec]:
        """The resolved model followed by its fallback chain, de-duplicated."""
        primary = self.resolve(name)
        chain = [primary]
        seen = {primary.alias}
        queue = list(primary.fallbacks)
        while queue and len(chain) < max_length:
            candidate = queue.pop(0)
            try:
                spec = self.resolve(candidate)
            except ModelNotRegistered:
                continue
            if spec.alias in seen:
                continue
            seen.add(spec.alias)
            chain.append(spec)
            queue.extend(spec.fallbacks)
        return chain

    def use_offline_models(self) -> ModelRegistry:
        """Repoint every task alias at the deterministic offline models."""
        for name, target in OFFLINE_ALIASES.items():
            self.alias(name, target)
        return self

    def known_names(self) -> set[str]:
        return set(self._specs) | set(self._aliases)

    def specs(self, *, modality: Modality | None = None) -> list[ModelSpec]:
        out = list(self._specs.values())
        if modality is not None:
            out = [s for s in out if s.modality == modality]
        return sorted(out, key=lambda s: s.alias)

    def aliases(self) -> dict[str, str]:
        return dict(self._aliases)

    # ---- YAML overrides -------------------------------------------------

    def load_yaml(self, path: str | Path) -> None:
        """Merge a ``models.yaml`` over the current registry.

        Schema::

            models:
              - alias: internal.summariser
                provider: openai
                provider_model: gpt-4o-mini
                input_cost_per_mtok: 0.15
                output_cost_per_mtok: 0.6
                fallbacks: [anthropic.claude-haiku-4-5]
            aliases:
              chat.default: internal.summariser
        """
        data = yaml.safe_load(Path(path).read_text()) or {}
        self.load_dict(data)

    def load_dict(self, data: dict[str, Any]) -> None:
        """Merge a parsed override document (see :meth:`load_yaml`)."""
        for raw in data.get("models") or []:
            if "alias" not in raw:
                raise ConfigError("each entry under `models:` needs an `alias`")
            alias = raw["alias"]
            base = self._specs.get(alias)
            fields: dict[str, Any] = {k: v for k, v in raw.items() if k != "alias"}
            if "fallbacks" in fields:
                fields["fallbacks"] = tuple(fields["fallbacks"] or ())
            if "tags" in fields:
                fields["tags"] = tuple(fields["tags"] or ())
            if base is None:
                missing = {"provider", "provider_model"} - fields.keys()
                if missing:
                    raise ConfigError(
                        f"new model {alias!r} is missing required field(s): "
                        f"{', '.join(sorted(missing))}"
                    )
                self.register(ModelSpec(alias=alias, **fields))
            else:
                self.register(replace(base, **fields))

        for name, target in (data.get("aliases") or {}).items():
            self.alias(name, target)
