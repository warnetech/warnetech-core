"""``warnetech custom`` command group: build and check custom models.

Every command takes at most four parameters (see :mod:`warnetech_core.cli_core`).
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .cli_core import Command, CommandGroup, Param, report_config_errors
from .config import Settings
from .custom import (
    CustomModel,
    CustomModelBuilder,
    load_cases,
    load_dataset,
    load_specs,
    run_eval,
    write_split,
)
from .errors import ConfigError

group = CommandGroup(
    "custom", "build, check and evaluate custom models on local Llama/Mistral/Gemma"
)

DEFAULT_SPECS_DIR = "config/custom_models"
DEFAULT_TRAINING_DIR = "./data/training"


def _emit(text: str) -> None:
    sys.stdout.write(text + "\n")


_guard = report_config_errors


def _specs_dir() -> Path:
    return Path(os.getenv("WARNETECH_CUSTOM_MODELS_DIR") or DEFAULT_SPECS_DIR)


def _find(name: str) -> tuple[CustomModel, Path]:
    directory = _specs_dir()
    for spec in load_specs(directory):
        if spec.name == name or spec.alias == name:
            return spec, directory
    raise ConfigError(f"no custom model named {name!r} in {directory}")


@_guard
def handle_list(_values: Mapping[str, Any]) -> int:
    specs = load_specs(_specs_dir())
    if not specs:
        _emit(f"no custom models in {_specs_dir()}")
        return 1
    _emit(f"{'NAME':<24} {'ALIAS':<22} {'BASE':<16} PROVIDER")
    for s in specs:
        _emit(f"{s.name:<24} {s.alias:<22} {s.base:<16} {s.provider}")
    return 0


@_guard
def handle_show(values: Mapping[str, Any]) -> int:
    spec, directory = _find(str(values["name"]))
    _emit(CustomModelBuilder().modelfile(spec, directory).rstrip("\n"))
    return 0


@_guard
def handle_create(values: Mapping[str, Any]) -> int:
    spec, directory = _find(str(values["name"]))
    builder = values.get("_builder") or CustomModelBuilder()
    builder.create(spec, directory)
    _emit(f"created {spec.name} (gateway alias: {spec.alias})")
    return 0


@_guard
def handle_dataset(values: Mapping[str, Any]) -> int:
    try:
        val_fraction = float(values.get("val-fraction") or 0.1)
        seed = int(values.get("seed") or 0)
    except ValueError as exc:
        raise ConfigError(f"val-fraction must be a number and seed a whole number: {exc}") from exc
    data = load_dataset(str(values["path"]))
    _emit(
        f"records: {len(data.records)}  duplicates dropped: {data.duplicates}  "
        f"problems: {len(data.issues)}"
    )
    for issue in data.issues[:20]:
        _emit(f"  line {issue.line}: {issue.message}")
    if data.issues:
        _emit("nothing written: fix the problems above first")
        return 1
    out_dir = str(values.get("out-dir") or DEFAULT_TRAINING_DIR)
    train, val = write_split(data.records, out_dir, val_fraction=val_fraction, seed=seed)
    _emit(f"wrote {train} training and {val} validation records to {out_dir}")
    if train and not val and val_fraction > 0:
        _emit("note: no record landed in validation. The split is by content hash, so a "
              "small file can end up all on one side.")
    return 0


@_guard
def handle_eval(values: Mapping[str, Any]) -> int:
    gateway = values.get("_gateway") or Settings.from_env().build_gateway()
    cases = load_cases(str(values["cases"]))
    limit = int(values["limit"]) if values.get("limit") else None
    model = str(values.get("model") or "chat.local")
    report = asyncio.run(run_eval(gateway, model, cases, limit=limit))
    for result in report.results:
        status = "pass" if result.passed else "FAIL"
        _emit(
            f"{status}  {result.case_id}"
            + ("" if result.passed else f"  ({'; '.join(result.failures)})")
        )
    _emit(f"{report.passed}/{report.total} passed on {model}")
    return 0 if report.passed == report.total else 1


group.register(Command("list", "custom models defined in the specs directory", handle_list))
group.register(
    Command(
        "show",
        "print the Modelfile a spec produces",
        handle_show,
        (Param("name", help="model name or gateway alias"),),
    )
)
group.register(
    Command(
        "create",
        "create the model in Ollama from its spec",
        handle_create,
        (Param("name", help="model name or gateway alias"),),
    )
)
group.register(
    Command(
        "dataset",
        "validate chat-format training data and split it into train and validation",
        handle_dataset,
        (
            Param("path", help="a .jsonl file of {messages: [...]} records"),
            Param(
                "out-dir",
                required=False,
                default=DEFAULT_TRAINING_DIR,
                help="where to write the split",
            ),
            Param(
                "val-fraction", required=False, default="0.1", help="share held out for validation"
            ),
            Param("seed", required=False, default="0", help="salt for the split"),
        ),
    )
)
group.register(
    Command(
        "eval",
        "run a set of prompts against a model alias and score the replies",
        handle_eval,
        (
            Param("cases", help="a YAML file with a `cases:` list"),
            Param("model", required=False, default="chat.local", help="registry alias to test"),
            Param("limit", required=False, default=None, help="run only the first N cases"),
        ),
    )
)
