# MCP Servers

MCP (Model Context Protocol) server implementations for warnetech-core.
Each server exposes tools that any MCP client can discover and invoke.

## warnetech\_mcp

The primary MCP server.  Exposes the gateway, memory/RAG, usage tracking,
and AGI orchestrator as MCP tools over JSON-RPC 2.0 / stdio.

### Quick start

```bash
# From the repo root (warnetech-core must be installed)
WARNETECH_OFFLINE=1 python -m warnetech_mcp
```

The server reads newline-delimited JSON-RPC from stdin and writes
responses to stdout.  Logs go to stderr.

### Available tools

| Tool | Description |
|------|-------------|
| `warnetech_complete` | Send a prompt through the AI gateway |
| `warnetech_embed` | Generate vector embeddings |
| `warnetech_remember` | Store a memory (namespaced by project) |
| `warnetech_recall` | Semantic search over stored memories |
| `warnetech_context` | RAG — retrieve grounded context for a question |
| `warnetech_forget` | Delete a memory by key |
| `warnetech_models` | List available models with pricing |
| `warnetech_usage` | Get usage summary (tokens, cost, requests) |
| `warnetech_run_goal` | Run a goal through the AGI orchestrator |
| `warnetech_interpret` | Propose code for a plain-language task (never executes it) |

### MCP client configuration

Add to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "warnetech": {
      "command": "python",
      "args": ["-m", "warnetech_mcp"],
      "env": {
        "PYTHONPATH": "/path/to/warnetech-core/mcp-servers:/path/to/warnetech-core/src",
        "WARNETECH_OFFLINE": "1"
      }
    }
  }
}
```

Set `WARNETECH_OFFLINE=0` and provide API keys (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`) to use real providers instead of the echo provider.

### Architecture

```
warnetech_mcp/
  __init__.py     Package metadata
  __main__.py     Entry point (python -m warnetech_mcp)
  server.py       JSON-RPC 2.0 stdio transport + MCP protocol
  tools.py        Tool schemas and async handlers
```

The server is zero-dependency beyond warnetech-core itself.  It lazily
builds the gateway and memory service on first tool call, so startup
is instant.
