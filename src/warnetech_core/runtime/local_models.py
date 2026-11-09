"""The local model catalogue: which models to install and which fit this host.

Reads the ``local:`` section of ``config/local_models.yaml``. The ``models:`` and
``aliases:`` sections are loaded separately by the registry.

Nothing here reaches the network. Installing a model is a separate, explicit
step through :class:`~warnetech_core.runtime.ollama_cli.OllamaCLI`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..errors import ConfigError
from .ollama_cli import validate_model_name
from .resources import Resources


@dataclass(frozen=True)
class LocalModel:
    name: str  # short role name, e.g. "general"
    model: str  # Ollama tag, e.g. "llama3.2:3b"
    role: str  # what it is chosen for
    approx_gb: float


@dataclass(frozen=True)
class PlanRow:
    model: LocalModel
    fits: bool


def load_catalogue(path: str | Path) -> list[LocalModel]:
    """Parse the ``local:`` section. Raises :class:`ConfigError` on a bad entry."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = data.get("local") or []
    models: list[LocalModel] = []
    seen: set[str] = set()
    for raw in entries:
        try:
            name = str(raw["name"])
            tag = validate_model_name(str(raw["model"]))
            role = str(raw.get("role", ""))
            size = float(raw["approx_gb"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"bad local model entry {raw!r}: {exc}") from exc
        if name in seen:
            raise ConfigError(f"duplicate local model name {name!r}")
        seen.add(name)
        models.append(LocalModel(name=name, model=tag, role=role, approx_gb=size))
    return models


def plan(catalogue: list[LocalModel], resources: Resources) -> list[PlanRow]:
    """Mark each catalogue model as fitting this host or not."""
    return [PlanRow(model=m, fits=resources.fits(m.approx_gb)) for m in catalogue]


def resolve_install_target(catalogue: list[LocalModel], target: str) -> str:
    """Map a role name (``general``) to its Ollama tag, or accept a raw tag.

    Raises :class:`ConfigError` for an unknown role with an invalid tag, so
    typos fail before anything is downloaded.
    """
    for model in catalogue:
        if model.name == target:
            return model.model
    return validate_model_name(target)
