"""Tool use framework for AGI capabilities.

Defines a :class:`ToolRegistry` for discovering and validating tools, and the
types (:class:`ToolDefinition`, :class:`ToolCall`, :class:`ToolResult`) that
flow through the system.  Designed to align with MCP tool schemas.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger("warnetech.agi.tools")

ToolHandler = Callable[..., Coroutine[Any, Any, Any]]


class ToolDefinition(BaseModel):
    """Schema for a callable tool, compatible with MCP tool format."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    required_params: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    def format_for_llm(self) -> dict[str, Any]:
        """Return the schema in the format expected by tool-use prompts."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.parameters,
                "required": self.required_params,
            },
        }


class ToolCall(BaseModel):
    """A request to invoke a tool with specific arguments."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = ""


class ToolResult(BaseModel):
    """The outcome of executing a tool call."""

    tool_name: str
    call_id: str = ""
    success: bool = True
    output: Any = None
    error: str | None = None
    elapsed_ms: float = 0.0


class ToolRegistry:
    """Registry of available tools with validation and execution.

    Example::

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(name="read_file", description="Read a file from disk"),
            handler=read_file_handler,
        )
        result = await registry.execute(ToolCall(tool_name="read_file", arguments={"path": "x"}))
    """

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(
        self,
        definition: ToolDefinition,
        *,
        handler: ToolHandler,
    ) -> None:
        """Register a tool with its handler."""
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    def unregister(self, name: str) -> None:
        self._definitions.pop(name, None)
        self._handlers.pop(name, None)

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def list_tools(self, *, tags: list[str] | None = None) -> list[ToolDefinition]:
        """List registered tools, optionally filtered by tags."""
        tools = list(self._definitions.values())
        if tags:
            tag_set = set(tags)
            tools = [t for t in tools if tag_set & set(t.tags)]
        return tools

    def format_for_llm(self) -> list[dict[str, Any]]:
        """Return all tool schemas formatted for an LLM tool-use prompt."""
        return [d.format_for_llm() for d in self._definitions.values()]

    async def execute(self, call: ToolCall) -> ToolResult:
        """Execute a tool call and return the result."""
        handler = self._handlers.get(call.tool_name)
        if handler is None:
            return ToolResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                success=False,
                error=f"unknown tool: {call.tool_name}",
            )

        started = time.perf_counter()
        try:
            output = await handler(**call.arguments)
            elapsed = (time.perf_counter() - started) * 1000
            return ToolResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                success=True,
                output=output,
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            log.error("tool %s failed: %s", call.tool_name, exc)
            return ToolResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=elapsed,
            )

    def validate_call(self, call: ToolCall) -> list[str]:
        """Validate a tool call against its definition. Returns error messages."""
        defn = self._definitions.get(call.tool_name)
        if defn is None:
            return [f"unknown tool: {call.tool_name}"]
        errors = []
        for param in defn.required_params:
            if param not in call.arguments:
                errors.append(f"missing required parameter: {param}")
        return errors
