# Credentials

None are needed for tests, CI or `-m echo/echo`. Fill in what you use. Never
put values in a config file: aicli reads keys from the environment variable
named by each provider's `api_key_env`.

| Variable | Used by | Required? | Status |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | `anthropic` provider (`sonnet`, `opus`, `haiku`) | For Claude | ☐ |
| `OPENAI_API_KEY` | `openai` provider (`gpt-4o`) | For OpenAI | ☐ |
| `DEEPSEEK_API_KEY` | the commented `[providers.deepseek]` example | Optional | ☐ |
| *(your name)* | any other OpenAI-compatible provider | Optional | ☐ |
| Ollama on `localhost:11434` (no key) | the default `local/llama3.2` | For local models | ☐ |
| Actions secret `ANTHROPIC_API_KEY` | `examples/github-actions/*` in a real repository | If adopted | ☐ |
| Node (`npx`) / uv (`uvx`) | the example MCP servers in `examples/mcp/config.toml` | For those servers | ☐ |

MCP servers that need a token, such as a GitHub server, inherit your shell
environment. Export the token there, not in `[mcp.servers.*.env]`.

Check: `aicli list providers` shows each provider, whether it is local or cloud,
and why it is or is not available. It never prints a key.
