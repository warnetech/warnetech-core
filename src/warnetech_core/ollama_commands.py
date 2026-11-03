"""``warnetech ollama`` command group, built on :mod:`warnetech_core.cli_core`.

Commands:
- ``status``: host resources and whether Ollama is reachable.
- ``models``: installed models with sizes and whether each fits this host.
- ``loaded``: models currently in memory.
- ``pull <model>``: download a model.
- ``recommend``: the largest installed model that fits this host.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any

from .cli_core import Command, CommandGroup, Param
from .errors import ConfigError
from .runtime import OllamaCLI, Resources, probe_resources, recommend_model

group = CommandGroup("ollama", "local models via the Ollama command-line tool")


def _emit(text: str) -> None:
    sys.stdout.write(text + "\n")


def _fmt_gb(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.1f} GB"


def describe_resources(res: Resources) -> str:
    return "\n".join(
        [
            f"cpu cores:      {res.cpu_count}",
            f"ram total:      {_fmt_gb(res.ram_total_gb)}",
            f"ram available:  {_fmt_gb(res.ram_available_gb)}",
            f"gpu vram total: {_fmt_gb(res.gpu_vram_total_gb)}",
            f"gpu vram free:  {_fmt_gb(res.gpu_vram_free_gb)}",
            f"model budget:   {_fmt_gb(res.budget_gb)}",
        ]
    )


def _cli(values: Mapping[str, Any]) -> OllamaCLI:
    # Injection point: the CLI passes an `_ollama` override in tests.
    return values.get("_ollama") or OllamaCLI()  # type: ignore[return-value]


def handle_status(values: Mapping[str, Any]) -> int:
    _emit(describe_resources(probe_resources()))
    try:
        loaded = _cli(values).loaded_models()
        _emit(f"ollama:         reachable ({len(loaded)} loaded)")
    except (ConfigError, OSError) as exc:
        _emit(f"ollama:         unavailable ({exc})")
        return 1
    return 0


def handle_models(values: Mapping[str, Any]) -> int:
    models = _cli(values).list_models()
    res = probe_resources()
    if not models:
        _emit("no models installed; try `warnetech ollama pull <model>`")
        return 0
    _emit(f"{'MODEL':<32} {'SIZE':>10}  FITS")
    for m in models:
        fits = "yes" if m.size_gb is not None and res.fits(m.size_gb) else "no"
        _emit(f"{m.name:<32} {_fmt_gb(m.size_gb):>10}  {fits}")
    return 0


def handle_loaded(values: Mapping[str, Any]) -> int:
    names = _cli(values).loaded_models()
    _emit("\n".join(names) if names else "no models loaded")
    return 0


def handle_pull(values: Mapping[str, Any]) -> int:
    name = str(values["model"])
    _cli(values).pull(name)
    _emit(f"pulled {name}")
    return 0


def handle_recommend(values: Mapping[str, Any]) -> int:
    models = _cli(values).list_models()
    pairs = [(m.name, m.size_gb) for m in models if m.size_gb is not None]
    choice = recommend_model(probe_resources(), pairs)
    if choice is None:
        _emit("no installed model fits this host")
        return 1
    _emit(choice)
    return 0


group.register(Command("status", "host resources and Ollama reachability", handle_status))
group.register(Command("models", "installed models and whether they fit", handle_models))
group.register(Command("loaded", "models currently loaded in memory", handle_loaded))
group.register(
    Command(
        "pull",
        "download a model",
        handle_pull,
        (Param("model", help="model name, e.g. llama3.1:8b"),),
    )
)
group.register(Command("recommend", "largest installed model that fits", handle_recommend))
