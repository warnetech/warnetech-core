"""A tiny stdio MCP server for tests: tools `add`, `shout` and `secret`."""

import json
import sys

TOOLS = [
    {"name": "add", "description": "Add two numbers",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"},
                                                      "b": {"type": "number"}}}},
    {"name": "shout", "description": "Upper-case text",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "secret", "description": "Must never be offered unless allowed",
     "inputSchema": {"type": "object"}},
]

print("not json-rpc: servers sometimes log to stdout", flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue  # notification
    method, params = msg["method"], msg.get("params", {})
    if method == "initialize":
        result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "0"}}
    elif method == "tools/list":
        # Paginate to exercise nextCursor handling.
        result = ({"tools": TOOLS[:1], "nextCursor": "2"} if not params.get("cursor")
                  else {"tools": TOOLS[1:]})
    elif method == "tools/call":
        args = params.get("arguments", {})
        if params["name"] == "add":
            result = {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}
        elif params["name"] == "shout":
            result = {"content": [{"type": "text", "text": args.get("text", "").upper()}]}
        else:
            result = {"content": [{"type": "text", "text": "the secret"}], "isError": False}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                          "error": {"code": -32601, "message": "no such method"}}), flush=True)
        continue
    # A server-initiated notification first, to check the client skips it.
    print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message",
                      "params": {"level": "info"}}), flush=True)
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
