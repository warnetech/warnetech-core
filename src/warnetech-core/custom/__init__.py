"""Custom models built on local Llama, Mistral and Gemma.

Four parts, each usable alone:

- ``spec``      a validated description of a model (base, system prompt, parameters)
- ``modelfile`` renders a spec as an Ollama Modelfile
- ``builder``   creates the model in Ollama
- ``dataset``   validates and splits chat-format training data
- ``evals``     checks a model alias against a set of prompts

Training a LoRA adapter happens outside this package, on a machine with a GPU. A
spec can point at the resulting adapter file.
"""

from __future__ import annotations

from pathlib import Path

from ..registry import ModelRegistry
from ..registry import ModelSpec as RegistryModel
from .builder import CustomModelBuilder, resolve_adapter
from .dataset import LoadedDataset, check_record, load_dataset, split, write_split
from .evals import EvalCase, EvalReport, check_reply, load_cases, run_eval
from .modelfile import render_modelfile
from .spec import CustomModel, load_spec, load_specs


def register_custom_models(registry: ModelRegistry, directory: str | Path) -> list[str]:
    """Add every spec in ``directory`` to ``registry`` and return the aliases.

    Registering only makes the alias resolvable. The model itself must exist in
    Ollama, which ``warnetech custom create`` takes care of.
    """
    aliases: list[str] = []
    for spec in load_specs(directory):
        registry.register(RegistryModel(
            alias=spec.alias,
            provider=spec.provider,
            provider_model=spec.name,
            context_window=int(spec.parameters.get("num_ctx", 8192)),
            max_output_tokens=2048,
            tags=("custom",),
        ))
        aliases.append(spec.alias)
    return aliases


__all__ = [
    "CustomModel",
    "CustomModelBuilder",
    "EvalCase",
    "EvalReport",
    "LoadedDataset",
    "check_record",
    "check_reply",
    "load_cases",
    "load_dataset",
    "load_spec",
    "load_specs",
    "register_custom_models",
    "render_modelfile",
    "resolve_adapter",
    "run_eval",
    "split",
    "write_split",
]
