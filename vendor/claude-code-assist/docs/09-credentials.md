# 09 · Credentials and external services

Everything in this repository runs offline with **no credentials**:
`make check`, the examples and CI. You only need the items below to call real
models or to publish. Fill in the **Status** column as you go. Never put a
value in a committed file. Put values in your shell, in `.env` (gitignored), or
in GitHub → Settings → Secrets.

## Model APIs

| Variable | Needed for | Required? | Where to get it | Status |
|---|---|---|---|---|
| `ANTHROPIC_API_KEY` | llmforge `AnthropicProvider`; aicli `anthropic` provider (`-m sonnet`, `opus`, `haiku`) | For any Claude call | console.anthropic.com → API keys | ☐ |
| `ANTHROPIC_AUTH_TOKEN` | llmforge only: an alternative to the key, or use `ant auth login`. aicli reads `ANTHROPIC_API_KEY` only. | Optional | your org's auth setup | ☐ |
| `OPENAI_API_KEY` | aicli `openai` provider (`-m gpt-4o`) | Only for OpenAI models | platform.openai.com → API keys | ☐ |
| `DEEPSEEK_API_KEY` | aicli, if you uncomment `[providers.deepseek]` | Optional | platform.deepseek.com | ☐ |
| *(your name)* | any other OpenAI-compatible endpoint: set `api_key_env` to a variable you choose | Optional | that vendor | ☐ |

## Local models (no key)

| Item | Needed for | How | Status |
|---|---|---|---|
| Ollama running on `localhost:11434` | aicli's default model (`local/llama3.2`) | install from ollama.com, then `ollama pull llama3.2` | ☐ |

## CI / GitHub

| Secret | Needed for | Required? | Status |
|---|---|---|---|
| *(none)* | this repo's own CI | — | ✅ |
| `ANTHROPIC_API_KEY` (Actions secret) | the aicli action examples (`packages/aicli/examples/github-actions`) when used in a real workflow | Only if you adopt them | ☐ |
| `OPENAI_API_KEY` (Actions secret) | the same, with OpenAI models | Optional | ☐ |

## Tooling and publishing

| Item | Needed for | Required? | Status |
|---|---|---|---|
| `CODEFLASH_API_KEY`, or `codeflash auth login` | the Codeflash performance hook | Optional | ☐ |
| PyPI Trusted Publishing (set up on pypi.org, no token) | releasing `llmforge` / `aicli` to PyPI. No release workflow exists yet. | Before a release | ☐ |
| npm token | releasing `@llmforge/core` | Before a release | ☐ |

## MCP servers (aicli `--mcp`)

The example servers (filesystem, git) need **no keys**, only a runtime: Node
for `npx` and uv for `uvx`. Servers that do need a token, such as a GitHub MCP
server, inherit your shell environment. Export the token there. Do not put it
under `[mcp.servers.*.env]`, because that writes the secret into a config file.

## Non-secret settings

| Variable | Effect | Default |
|---|---|---|
| `LLMFORGE_TRACE` | where llmforge writes its JSONL trace | `.llmforge/trace.jsonl` |
| `LLMFORGE_MODEL` | default model for examples and labs | `claude-opus-5` |
| `AICLI_HOME` | aicli's config, history, audit log and trust file | `~/.aicli` |

`AICLI_TEST_KEY` appears in aicli's tests. The tests set it themselves; it is
never a real key.

## Check what is set

```bash
make doctor        # llmforge credential source + aicli provider reachability; never prints values
```
