"""``warnetech route`` command group: see and use task routing.

``explain`` shows which model a task would go to and why. ``ask`` sends it there.
``pipeline`` runs a chain of models. Each command takes at most four parameters.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping
from typing import Any

from .cli_core import Command, CommandGroup, Param, report_config_errors
from .config import Settings
from .routing import KINDS, TaskRouter, load_pipeline, load_table, run_pipeline

group = CommandGroup("route", "send each task to the local model suited to it")

DEFAULT_TABLE = "config/routing.yaml"
KIND_CHOICES = KINDS


def _emit(text: str) -> None:
    sys.stdout.write(text + "\n")


def _build(values: Mapping[str, Any]):
    gateway = values.get("_gateway") or Settings.from_env().build_gateway()
    table, default = load_table(os.getenv("WARNETECH_ROUTING_FILE") or DEFAULT_TABLE)
    return gateway, TaskRouter.for_gateway(gateway, table, default=default)


@report_config_errors
def handle_explain(values: Mapping[str, Any]) -> int:
    _, router = _build(values)
    route = router.route(str(values["text"]), kind=values.get("kind"))
    _emit(f"kind:   {route.kind}  ({route.reason})")
    _emit(f"model:  {route.alias}")
    for alias, why in route.skipped:
        _emit(f"skipped {alias}: {why}")
    return 0


@report_config_errors
def handle_ask(values: Mapping[str, Any]) -> int:
    gateway, router = _build(values)
    text = str(values["text"])
    route = router.route(text, kind=values.get("kind"))
    reply = asyncio.run(gateway.complete(route.alias, text))
    _emit(reply.text)
    sys.stderr.write(f"[{route.kind} -> {route.alias}]\n")
    return 0


@report_config_errors
def handle_pipeline(values: Mapping[str, Any]) -> int:
    gateway, router = _build(values)
    pipeline = load_pipeline(str(values["file"]))
    result = asyncio.run(run_pipeline(gateway, pipeline, str(values["text"]), router=router))
    for step in result.steps:
        _emit(f"--- {step.name} ({step.model})")
        _emit(step.output.rstrip())
    return 0


group.register(Command(
    "explain",
    "show which model a task would go to, and why",
    handle_explain,
    (
        Param("text", help="the task, in plain language"),
        Param(
            "kind", required=False, default=None, choices=KIND_CHOICES,
            help="override the automatic guess",
        ),
    ),
))
group.register(Command(
    "ask",
    "send a task to the model suited to it",
    handle_ask,
    (
        Param("text", help="the task, in plain language"),
        Param(
            "kind", required=False, default=None, choices=KIND_CHOICES,
            help="override the automatic guess",
        ),
    ),
))
group.register(Command(
    "pipeline",
    "run a chain of models on some text",
    handle_pipeline,
    (
        Param("file", help="a pipeline YAML file (see config/pipelines/)"),
        Param("text", help="the text to process"),
    ),
))
