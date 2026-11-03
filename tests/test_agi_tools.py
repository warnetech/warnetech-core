"""Tests for warnetech_core.agi.tools."""

from __future__ import annotations

import pytest

from warnetech_core.agi.tools import (
    ToolCall,
    ToolDefinition,
    ToolRegistry,
)


class TestToolDefinition:
    def test_format_for_llm(self):
        defn = ToolDefinition(
            name="read",
            description="Read a file",
            parameters={"path": {"type": "string"}},
            required_params=["path"],
        )
        out = defn.format_for_llm()
        assert out["name"] == "read"
        assert out["input_schema"]["required"] == ["path"]


class TestToolRegistry:
    @pytest.fixture
    def registry(self):
        r = ToolRegistry()

        async def echo_handler(**kwargs):
            return kwargs

        r.register(
            ToolDefinition(
                name="echo",
                description="Echo args",
                parameters={"msg": {"type": "string"}},
                required_params=["msg"],
                tags=["test"],
            ),
            handler=echo_handler,
        )
        return r

    def test_get(self, registry):
        assert registry.get("echo") is not None
        assert registry.get("nope") is None

    def test_list_tools(self, registry):
        assert len(registry.list_tools()) == 1
        assert len(registry.list_tools(tags=["test"])) == 1
        assert len(registry.list_tools(tags=["other"])) == 0

    def test_format_for_llm(self, registry):
        schemas = registry.format_for_llm()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "echo"

    def test_validate_call_ok(self, registry):
        call = ToolCall(tool_name="echo", arguments={"msg": "hi"})
        assert registry.validate_call(call) == []

    def test_validate_call_missing(self, registry):
        call = ToolCall(tool_name="echo", arguments={})
        errors = registry.validate_call(call)
        assert len(errors) == 1
        assert "msg" in errors[0]

    def test_validate_call_unknown(self, registry):
        call = ToolCall(tool_name="nope", arguments={})
        errors = registry.validate_call(call)
        assert "unknown" in errors[0]

    @pytest.mark.asyncio
    async def test_execute_success(self, registry):
        call = ToolCall(tool_name="echo", arguments={"msg": "hello"})
        result = await registry.execute(call)
        assert result.success is True
        assert result.output == {"msg": "hello"}

    @pytest.mark.asyncio
    async def test_execute_unknown(self, registry):
        call = ToolCall(tool_name="nope", arguments={})
        result = await registry.execute(call)
        assert result.success is False
        assert "unknown" in result.error

    @pytest.mark.asyncio
    async def test_execute_error(self):
        r = ToolRegistry()

        async def failing(**kwargs):
            raise ValueError("boom")

        r.register(
            ToolDefinition(name="fail", description="always fails"),
            handler=failing,
        )
        result = await r.execute(ToolCall(tool_name="fail"))
        assert result.success is False
        assert "boom" in result.error

    def test_unregister(self, registry):
        registry.unregister("echo")
        assert registry.get("echo") is None
