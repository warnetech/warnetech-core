"""MCP server — JSON-RPC 2.0 over stdio.

Implements the Model Context Protocol handshake (``initialize`` /
``initialized``), tool listing (``tools/list``) and tool invocation
(``tools/call``).  The transport is newline-delimited JSON on stdin/stdout,
which is what MCP clients expect for a stdio-based server.

Zero external dependencies — only stdlib + warnetech_core.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from .tools import TOOL_SCHEMAS, dispatch_tool

log = logging.getLogger("warnetech.mcp")

_SERVER_INFO = {
    "name": "warnetech-mcp",
    "version": "0.1.0",
}

_CAPABILITIES = {
    "tools": {},
}


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------


def _ok(id_: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


# ---------------------------------------------------------------------------
# Method handlers
# ---------------------------------------------------------------------------


def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
    """Respond to the MCP ``initialize`` handshake."""
    return {
        "protocolVersion": "2024-11-05",
        "serverInfo": _SERVER_INFO,
        "capabilities": _CAPABILITIES,
    }


def _handle_tools_list(_params: dict[str, Any]) -> dict[str, Any]:
    return {"tools": TOOL_SCHEMAS}


async def _handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name", "")
    arguments = params.get("arguments", {})
    try:
        result = await dispatch_tool(name, arguments)
        return {
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
            "isError": False,
        }
    except Exception as exc:
        log.error("tool %s failed: %s", name, exc)
        return {
            "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
            "isError": True,
        }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_SYNC_METHODS: dict[str, Any] = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
}

_ASYNC_METHODS: dict[str, Any] = {
    "tools/call": _handle_tools_call,
}

# Notifications (no response required)
_NOTIFICATIONS = {"notifications/initialized", "initialized"}


async def _dispatch(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method", "")
    id_ = msg.get("id")
    params = msg.get("params", {})

    # Notifications — no response
    if method in _NOTIFICATIONS:
        return None

    if id_ is None:
        # Unknown notification — silently ignore per JSON-RPC spec
        return None

    if method in _SYNC_METHODS:
        result = _SYNC_METHODS[method](params)
        return _ok(id_, result)

    if method in _ASYNC_METHODS:
        result = await _ASYNC_METHODS[method](params)
        return _ok(id_, result)

    return _error(id_, -32601, f"Method not found: {method}")


# ---------------------------------------------------------------------------
# Stdio transport
# ---------------------------------------------------------------------------


async def _run() -> None:
    """Read JSON-RPC messages from stdin, dispatch, write responses to stdout."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    # We need a raw binary writer for stdout
    transport, _ = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.BaseProtocol, sys.stdout
    )

    while True:
        line = await reader.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = _error(None, -32700, "Parse error")
            transport.write((json.dumps(resp) + "\n").encode())
            continue

        resp = await _dispatch(msg)
        if resp is not None:
            transport.write((json.dumps(resp) + "\n").encode())


def main() -> None:
    """Entry point — blocks on the async event loop."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,  # MCP logs go to stderr, stdout is the RPC channel
    )
    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, EOFError):
        pass
