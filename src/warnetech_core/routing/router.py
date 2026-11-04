"""Send each task to the model best suited to it, from what is actually available.

The routing table lists, per kind of task, model aliases in order of preference.
The router picks the first one whose provider is registered in this process, so
the same table works with hosted models, with local ones, and in
``WARNETECH_LOCAL_ONLY`` mode, where hosted aliases are skipped automatically.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError
from ..gateway import AIGateway
from .classify import KINDS, classify

_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")


class RoutingError(ConfigError):
    """No model could be chosen for a task."""


@dataclass(frozen=True)
class Route:
    kind: str
    alias: str
    reason: str
    skipped: tuple[tuple[str, str], ...] = ()


def load_table(path: str | Path) -> tuple[dict[str, list[str]], str]:
    """Read a routing file. Returns ``(table, default_kind)``."""
    file = Path(path)
    try:
        data: Any = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {file}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), dict):
        raise ConfigError(f"{file} needs a `tasks:` mapping")

    table: dict[str, list[str]] = {}
    for kind, aliases in data["tasks"].items():
        if kind not in KINDS:
            raise ConfigError(f"{file.name}: unknown task kind {kind!r}; use one of {KINDS}")
        if not isinstance(aliases, list) or not aliases:
            raise ConfigError(f"{file.name}: {kind!r} needs a non-empty list of aliases")
        for alias in aliases:
            if not isinstance(alias, str) or not _ALIAS_RE.fullmatch(alias):
                raise ConfigError(f"{file.name}: bad alias {alias!r} under {kind!r}")
        table[kind] = list(aliases)

    default = str(data.get("default", "chat"))
    if default not in table:
        raise ConfigError(f"{file.name}: default kind {default!r} has no entry under `tasks:`")
    return table, default


class TaskRouter:
    def __init__(
        self,
        table: Mapping[str, Sequence[str]],
        *,
        default: str = "chat",
        is_available: Callable[[str], bool] = lambda _alias: True,
    ) -> None:
        if default not in table:
            raise RoutingError(f"default kind {default!r} is not in the routing table")
        self.table = {k: list(v) for k, v in table.items()}
        self.default = default
        self._is_available = is_available

    @classmethod
    def for_gateway(
        cls, gateway: AIGateway, table: Mapping[str, Sequence[str]], *, default: str = "chat"
    ) -> TaskRouter:
        """A router that only picks aliases the gateway can actually call."""

        def available(alias: str) -> bool:
            try:
                return gateway.registry.resolve(alias).provider in gateway.providers
            except ConfigError:  # unknown alias
                return False

        return cls(table, default=default, is_available=available)

    def route(self, text: str, kind: str | None = None) -> Route:
        """Choose a model for ``text``. ``kind`` overrides the classifier."""
        if kind is not None:
            if kind not in self.table:
                raise RoutingError(f"no route for kind {kind!r}; known: {sorted(self.table)}")
            chosen_kind, why = kind, "kind given explicitly"
        else:
            guess = classify(text)
            chosen_kind, why = guess.kind, guess.reason
            if chosen_kind not in self.table:
                chosen_kind, why = self.default, f"{why}; no route for it, using {self.default}"

        skipped: list[tuple[str, str]] = []
        for alias in self.table[chosen_kind]:
            if self._is_available(alias):
                return Route(chosen_kind, alias, why, tuple(skipped))
            skipped.append((alias, "provider not available here"))
        tried = ", ".join(a for a, _ in skipped)
        raise RoutingError(f"no available model for {chosen_kind!r} (tried: {tried})")
