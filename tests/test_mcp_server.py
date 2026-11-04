"""Tests for the MCP server tool dispatch and schemas."""

from __future__ import annotations

import json
import os
import sys

# Ensure mcp-servers is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-servers"))

os.environ.setdefault("WARNETECH_OFFLINE", "1")

from warnetech_mcp.server import _dispatch, _error, _ok  # noqa: E402
from warnetech_mcp.tools import TOOL_SCHEMAS  # noqa: E402


class TestJsonRpcHelpers:
    def test_ok_response(self):
        resp = _ok(1, {"foo": "bar"})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert resp["result"] == {"foo": "bar"}

    def test_error_response(self):
        resp = _error(2, -32600, "Invalid request")
        assert resp["error"]["code"] == -32600
        assert resp["error"]["message"] == "Invalid request"
        assert resp["id"] == 2

    def test_error_with_data(self):
        resp = _error(3, -1, "fail", data={"detail": "x"})
        assert resp["error"]["data"] == {"detail": "x"}


class TestToolSchemas:
    def test_all_schemas_have_required_fields(self):
        for schema in TOOL_SCHEMAS:
            assert "name" in schema, f"Schema missing 'name': {schema}"
            assert "description" in schema, f"{schema['name']} missing 'description'"
            assert "inputSchema" in schema, f"{schema['name']} missing 'inputSchema'"

    def test_expected_tools_present(self):
        names = {s["name"] for s in TOOL_SCHEMAS}
        expected = {
            "warnetech_complete",
            "warnetech_embed",
            "warnetech_remember",
            "warnetech_recall",
            "warnetech_context",
            "warnetech_forget",
            "warnetech_models",
            "warnetech_usage",
            "warnetech_run_goal",
        }
        assert expected <= names

    def test_schemas_are_valid_json(self):
        # Verify round-trip serialization
        text = json.dumps(TOOL_SCHEMAS)
        loaded = json.loads(text)
        assert len(loaded) == len(TOOL_SCHEMAS)


class TestDispatch:
    async def test_initialize(self):
        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        resp = await _dispatch(msg)
        assert resp["id"] == 1
        result = resp["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "warnetech-mcp"
        assert "tools" in result["capabilities"]

    async def test_tools_list(self):
        msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        resp = await _dispatch(msg)
        tools = resp["result"]["tools"]
        assert len(tools) >= 9
        names = {t["name"] for t in tools}
        assert "warnetech_complete" in names

    async def test_initialized_notification(self):
        msg = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        resp = await _dispatch(msg)
        assert resp is None  # notifications get no response

    async def test_unknown_method(self):
        msg = {"jsonrpc": "2.0", "id": 3, "method": "bogus/method", "params": {}}
        resp = await _dispatch(msg)
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    async def test_tools_call_complete(self):
        """Test a tool call using the echo provider (offline mode)."""
        msg = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "warnetech_complete",
                "arguments": {"prompt": "hello", "project": "test"},
            },
        }
        resp = await _dispatch(msg)
        assert resp["id"] == 4
        result = resp["result"]
        assert result["isError"] is False
        content = json.loads(result["content"][0]["text"])
        assert "text" in content

    async def test_tools_call_models(self):
        msg = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "warnetech_models", "arguments": {}},
        }
        resp = await _dispatch(msg)
        content = json.loads(resp["result"]["content"][0]["text"])
        assert "models" in content
        assert content["count"] > 0

    async def test_tools_call_unknown_tool(self):
        msg = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        }
        resp = await _dispatch(msg)
        result = resp["result"]
        assert result["isError"] is True

    async def test_tools_call_remember_and_recall(self):
        """Round-trip: store a memory, then recall it."""
        # Remember
        msg_remember = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "warnetech_remember",
                "arguments": {
                    "project": "mcp-test",
                    "content": "The database is Postgres for billing.",
                    "key": "billing-db",
                },
            },
        }
        resp = await _dispatch(msg_remember)
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["stored"] is True

        # Recall
        msg_recall = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "warnetech_recall",
                "arguments": {
                    "project": "mcp-test",
                    "query": "which database for billing?",
                },
            },
        }
        resp = await _dispatch(msg_recall)
        content = json.loads(resp["result"]["content"][0]["text"])
        assert len(content["hits"]) > 0
        assert "Postgres" in content["hits"][0]["content"]

    async def test_tools_call_context(self):
        """Test RAG context retrieval."""
        # First store something
        await _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "warnetech_remember",
                    "arguments": {
                        "project": "mcp-ctx-test",
                        "content": "We use FastAPI for the service layer.",
                        "key": "stack-api",
                    },
                },
            }
        )

        # Then get context
        resp = await _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "warnetech_context",
                    "arguments": {
                        "project": "mcp-ctx-test",
                        "question": "What framework for the API?",
                    },
                },
            }
        )
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["source_count"] > 0
        assert "FastAPI" in content["text"]

    async def test_tools_call_usage(self):
        msg = {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "warnetech_usage", "arguments": {}},
        }
        resp = await _dispatch(msg)
        content = json.loads(resp["result"]["content"][0]["text"])
        assert "total_requests" in content

    async def test_tools_call_forget(self):
        # Store
        await _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "warnetech_remember",
                    "arguments": {
                        "project": "mcp-del-test",
                        "content": "temporary data",
                        "key": "temp-key",
                    },
                },
            }
        )

        # Forget
        resp = await _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "name": "warnetech_forget",
                    "arguments": {"project": "mcp-del-test", "key": "temp-key"},
                },
            }
        )
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["deleted"] is True
