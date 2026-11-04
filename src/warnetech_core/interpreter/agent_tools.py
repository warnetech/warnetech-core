"""Expose the interpreter to agents as tools on a :class:`ToolRegistry`.

File tools are always confined to the workspace. ``run_code`` is registered only
when the caller supplies BOTH an executor and an approver, so an agent cannot be
given code execution without someone deciding how it is approved.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..agi.tools import ToolDefinition, ToolRegistry
from .audit import AuditLog
from .executor import Executor
from .policy import Action, Approver
from .workspace import Workspace


def register_interpreter_tools(
    registry: ToolRegistry,
    workspace: Workspace,
    *,
    executor: Executor | None = None,
    approver: Approver | None = None,
    audit: AuditLog | None = None,
) -> list[str]:
    """Register the tools and return their names."""
    log = audit or AuditLog(None)

    async def workspace_read(path: str) -> str:
        return workspace.read_text(path)

    async def workspace_write(path: str, content: str) -> dict[str, Any]:
        written = workspace.write_text(path, content)
        log.record("file_write", path=path, bytes=written)
        return {"path": path, "bytes": written}

    async def workspace_list(path: str = ".") -> list[str]:
        return workspace.list_dir(path)

    registry.register(
        ToolDefinition(
            name="workspace_read",
            description="Read a text file inside the workspace.",
            parameters={
                "path": {"type": "string", "description": "path relative to the workspace"}
            },
            required_params=["path"],
            tags=["interpreter", "files"],
        ),
        handler=workspace_read,
    )
    registry.register(
        ToolDefinition(
            name="workspace_write",
            description="Create or overwrite a text file inside the workspace.",
            parameters={
                "path": {"type": "string", "description": "path relative to the workspace"},
                "content": {"type": "string", "description": "file contents"},
            },
            required_params=["path", "content"],
            tags=["interpreter", "files"],
        ),
        handler=workspace_write,
    )
    registry.register(
        ToolDefinition(
            name="workspace_list",
            description="List a directory inside the workspace.",
            parameters={"path": {"type": "string", "description": "directory, default '.'"}},
            tags=["interpreter", "files"],
        ),
        handler=workspace_list,
    )
    names = ["workspace_read", "workspace_write", "workspace_list"]

    if executor is not None and approver is not None:

        async def run_code(language: str, code: str) -> dict[str, Any]:
            action = Action(language=language, code=code, workspace=str(workspace.root))
            if not approver(action):
                log.record("denied", language=language, code=code)
                return {"approved": False}
            result = await asyncio.to_thread(executor.run, language, code)
            log.record(
                "executed",
                language=language,
                code=code,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )
            return {
                "approved": True,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "truncated": result.truncated,
            }

        registry.register(
            ToolDefinition(
                name="run_code",
                description="Run python or bash in the workspace, after approval.",
                parameters={
                    "language": {"type": "string", "enum": list(executor.allowed_languages)},
                    "code": {"type": "string"},
                },
                required_params=["language", "code"],
                tags=["interpreter", "exec"],
            ),
            handler=run_code,
        )
        names.append("run_code")
    return names
