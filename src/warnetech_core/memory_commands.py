"""``warnetech memory`` command group: ingest documents and manage core memory."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from typing import Any

from .chat import CORE_SCOPE
from .cli_core import Command, CommandGroup, Param, report_config_errors
from .config import build
from .memory.ingest import ingest_path

group = CommandGroup("memory", "documents, core facts and recall in your own memory")


def _emit(text: str) -> None:
    sys.stdout.write(text + "\n")


def _build(values: Mapping[str, Any]):
    return values.get("_built") or build(project=str(values.get("project") or "default"))


async def _with_memory(values: Mapping[str, Any], action):
    gateway, memory = _build(values)
    try:
        return await action(memory)
    finally:
        await memory.aclose()
        await gateway.aclose()


@report_config_errors
def handle_ingest(values: Mapping[str, Any]) -> int:
    project = str(values.get("project") or "default")

    async def action(memory):
        return await ingest_path(memory, project, str(values["path"]))

    try:
        report = asyncio.run(_with_memory(values, action))
    except FileNotFoundError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    _emit(f"ingested {report.files} files, {report.chunks} chunks, removed {report.removed}")
    for name, why in report.skipped:
        sys.stderr.write(f"skipped {name}: {why}\n")
    return 0


@report_config_errors
def handle_core_add(values: Mapping[str, Any]) -> int:
    project = str(values.get("project") or "default")

    async def action(memory):
        return await memory.remember(project, str(values["text"]), scope=CORE_SCOPE)

    rec = asyncio.run(_with_memory(values, action))
    _emit(f"pinned {rec.id}")
    return 0


@report_config_errors
def handle_core_list(values: Mapping[str, Any]) -> int:
    project = str(values.get("project") or "default")

    async def action(memory):
        return await memory.list(project, scope=CORE_SCOPE, limit=200)

    for rec in asyncio.run(_with_memory(values, action)):
        _emit(f"{rec.id}  {rec.text}")
    return 0


@report_config_errors
def handle_core_forget(values: Mapping[str, Any]) -> int:
    project = str(values.get("project") or "default")

    async def action(memory):
        return await memory.forget(project, id=str(values["id"]), scope=CORE_SCOPE)

    removed = asyncio.run(_with_memory(values, action))
    _emit(f"removed {removed}")
    return 0 if removed else 1


_PROJECT = Param("project", required=False, default="default", help="memory project name")
group.register(Command("ingest", "chunk, embed and store a file or folder", handle_ingest,
                       (Param("path", help="file or folder of text"), _PROJECT)))
group.register(Command("core-add", "pin a fact that is always in context", handle_core_add,
                       (Param("text", help="the fact"), _PROJECT)))
group.register(Command("core-list", "show pinned facts", handle_core_list, (_PROJECT,)))
group.register(Command("core-forget", "unpin a fact by id", handle_core_forget,
                       (Param("id", help="id from core-list"), _PROJECT)))
