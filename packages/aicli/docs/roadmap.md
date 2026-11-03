# Roadmap and coverage

This file tracks the PRD's **gap analysis** and **personas** against what is
built. It is updated with the code, so it can be trusted over the PRD's own
checkboxes. ✅ built and tested · ⚠️ built with a stated limit · ❌ not built yet.

## Gap analysis → features

### 1. Unified CLI

| Gap | Status | How |
|---|---|---|
| Multiple providers **and** local models in one command | ✅ | `aicli "…" -m sonnet` / `-m local/llama3.2` / `-m openai/gpt-4o`. Any OpenAI-compatible API (DeepSeek, vLLM, LM Studio) via `type = "openai"` + `base_url`. |
| Cost-aware routing, native (no separate gateway) | ✅ | `--route cheapest\|fastest\|best`, `--min-quality N`, `--max-cost USD` over `[routing] pool`. `router.py` |
| Works entirely offline, graceful cloud fallback | ✅ | Default model is local. Cloud fallback is opt-in (`fallback = ["sonnet"]`). History, search, templates and stats never need the network. |
| MCP servers as first-class citizens | ⚠️ | `[mcp.servers.*]`, `aicli mcp list/tools/call`, `aicli "…" --mcp NAME` runs a tool loop on Anthropic, OpenAI-compatible and Ollama models. **Limit:** stdio transport and tools only. HTTP transport, resources and prompts are not built. `mcp.py` |

### 2. Privacy by default

| Gap | Status | How |
|---|---|---|
| Local by default, cloud on demand | ✅ | Default `local/llama3.2`. `local_only = true` (or `--local-only`) refuses every cloud call. `--allow-cloud` lifts it for one command. A cloned repo's `.aicli.toml` is ignored until `aicli project trust`. |
| Audit log of every API call | ✅ | `~/.aicli/audit.jsonl`: destination host, local/cloud, bytes out and a prompt hash, never the prompt text. `aicli audit --cloud` shows what left the machine. Each cloud call is also announced on stderr. `audit.py` |
| No external account needed | ✅ | Works with only Ollama, or with nothing at all (`-m echo/echo`). |

### 3. Cost transparency

| Gap | Status | How |
|---|---|---|
| Estimate before execution | ✅ | Worst-case estimate on stderr before every call. |
| Route on cost / latency / accuracy | ⚠️ | Cost comes from the price table. Latency is learned from your own history. **Limit:** "accuracy" is a 1–5 quality score you assign in `[routing.quality]`. Nothing measures it yet. |
| Cost breakdown per prompt and per model | ✅ | `history` shows tokens and cost for each prompt. `stats [--since today\|7d\|month]` totals per model. `budget` compares spend with your limits. |
| Budgets | ✅ | `[budget] daily_usd / monthly_usd`. A call that could cross a limit is refused (exit 3) unless you pass `--over-budget`. **Limit:** checked per process, so two processes running at once can both pass. |

### 4. Multi-turn and project context

| Gap | Status | How |
|---|---|---|
| Conversation state persisted locally | ✅ | Every chat turn is saved. Pick one up again with `aicli chat --resume ID` or `--continue`. List them with `aicli sessions`. |
| Prompt fragments and template reuse | ✅ | `-t NAME --var k=v`, `{{input}}`, `{{> fragment}}`, and TOML front matter for model, system and max_tokens. `aicli templates list/show`. `templates.py` |
| Integrates with project structure | ✅ | `aicli project init` creates `.aicli.toml` and `.aicli/templates`, which are found from any subdirectory. `-F path` adds files as context (capped at 200 KB each). `history --project` filters to this repo. |

## Personas

### Persona 1: Privacy-focused developer: **covered**
- Local models via Ollama ✅ · nothing goes to the cloud unless chosen ✅ (`local_only`, opt-in fallback, project trust)
- Can see what leaves the machine ✅ (`aicli audit --cloud`, stderr notices)
- Offline environments ✅. Installing needs the network once, or a wheel copied in.

### Persona 2: Cost-conscious startup: **covered, with two limits**
- Claude for reasoning, DeepSeek for bulk, local Phi for volume ✅ (one config, three providers)
- Automatic routing to cut spend ✅ (`--route cheapest --min-quality 3`)
- Cost tracking and budgets ✅ ⚠️ per-process enforcement (see above)
- CI/CD batch processing ✅ (`aicli batch -j 8`, `action.yml`, `examples/github-actions/nightly-batch.yml`)
- ⚠️ Quality scores are hand-set, so "cheapest model that is good enough" is only as good as your scores.

### Persona 3: Researcher / prompt engineer: **covered**
- Iterate across models side by side ✅ (`aicli compare -m a -m b -m c`)
- Reproducible runs ✅ (`export ID -o runs/x.json` saves the prompt, system, temperature, seed and template; `rerun runs/x.json [-m other]`). Providers themselves are not fully deterministic, even with a seed.
- Version control and Git workflows ✅ (templates and exported runs live in the repo; `git diff | aicli -t review`)
- ❌ Automatic scoring of outputs (an eval harness). Planned for Phase 3, reusing `llmforge.evals` in claude-code-assist.

### Persona 4: AI automation builder: **mostly covered**
- Scripted multi-model workflows ✅ (stable JSON output, exit codes 0/1/2/3, stdin)
- MCP for external data ⚠️ (stdio tools only, see above)
- JSON output for piping ✅ · GitHub Actions ✅ (`action.yml`, tested by CI)
- ❌ **Zapier / Make.com.** These call webhooks, and a CLI cannot receive one. This needs an `aicli serve` HTTP endpoint with authentication. Planned for Phase 3.

## Still needed for "complete"

1. `aicli serve`: an authenticated HTTP endpoint for Zapier, Make and webhooks (Persona 4).
2. MCP over streamable HTTP, plus resources and prompts.
3. A quality signal that is measured, not assigned: an eval harness scoring `compare` and `batch` output (Personas 2 and 3).
4. Budgets enforced across processes (a lock or ledger row), not per process.
5. Streaming output for long answers.
6. A PyPI release.
7. A live check against real Anthropic, OpenAI and Ollama endpoints. Today they are tested against stubbed HTTP only.
8. PRD Phase 3 items: web dashboard, Slack/Discord bot, team budgets.
