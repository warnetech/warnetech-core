import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("MCPServer")

ToolHandler = Callable[..., Awaitable[Any]]

class MCPServer:
    def __init__(self, name: str = "warnetech-mcp-server") -> None:
        self.name = name
        self.tools: dict[str, ToolHandler] = {}
        self._running = False

    def register_tool(self, name: str, handler: ToolHandler) -> None:
        """Register an executable tool function with the MCP server."""
        self.tools[name] = handler
        logger.info(f"Registered MCP tool: '{name}'")

    async def start(self) -> None:
        """Initialize the MCP server background listener loop."""
        self._running = True
        logger.info(f"MCP Server '{self.name}' started successfully.")

    async def handle_request(self, raw_message: str) -> str:
        """Process incoming JSON-RPC 2.0 requests for tool execution."""
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as e:
            return json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {e}"},
                "id": None
            })

        req_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})

        if method == "tools/list":
            return json.dumps({
                "jsonrpc": "2.0",
                "result": {"tools": [{"name": t} for t in self.tools.keys()]},
                "id": req_id
            })

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if tool_name not in self.tools:
                return json.dumps({
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
                    "id": req_id
                })

            try:
                handler = self.tools[tool_name]
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(**arguments)
                else:
                    result = handler(**arguments)

                return json.dumps({
                    "jsonrpc": "2.0",
                    "result": {"content": [{"type": "text", "text": str(result)}]},
                    "id": req_id
                })
            except Exception as e:
                logger.error(f"Error executing tool '{tool_name}': {e}", exc_info=True)
                return json.dumps({
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": str(e)},
                    "id": req_id
                })

        return json.dumps({
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": f"Method not found: {method}"},
            "id": req_id
        })

    async def stop(self) -> None:
        """Gracefully shut down the MCP server."""
        self._running = False
        self.tools.clear()
        logger.info(f"MCP Server '{self.name}' stopped.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def sample_echo_tool(message: str) -> str:
        return f"MCP Echo Response: {message}"

    async def test():
        server = MCPServer()
        server.register_tool("echo", sample_echo_tool)
        await server.start()

        req = json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "Hello AGI Framework"}},
            "id": 1
        })

        response = await server.handle_request(req)
        print("MCP Server test response:", response)
        await server.stop()

    asyncio.run(test())
