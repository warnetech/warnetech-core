# aicli — CLI-to-AI Command Center

**One command, any model.** A local-first CLI for Claude, OpenAI-compatible APIs
and Ollama, with:
- cost-, latency- and quality-aware routing
- budgets
- MCP tools
- templates
- resumable chats
- an audit log of everything that leaves your machine

There are no runtime dependencies.

- Product plan: [`CLI-AI-Command-Center-PRD.md`](CLI-AI-Command-Center-PRD.md)
- What is built, mapped to the PRD's gaps and personas: [`docs/roadmap.md`](docs/roadmap.md)
- Where and why this departs from the PRD: [`docs/decisions.md`](docs/decisions.md)
- Keys and services to set up: [`docs/credentials.md`](docs/credentials.md)

```bash
pip install -e ".[dev]"
aicli "hello" -m echo/echo        # offline: no keys, no Ollama needed
aicli config init                 # writes ~/.aicli/config.toml
aicli list providers              # what is reachable, local vs cloud, and why not
```

## Everyday use

```bash
aicli "Explain quantum computing"                     # default model (local Ollama)
aicli "Explain quantum computing" -m sonnet -f json | jq .cost_usd
git diff | aicli -m opus -s "Review this diff" -      # prompt from stdin
aicli "Why does this fail?" -F src/app.py -F tests/test_app.py
aicli chat -m local/mistral                           # :quit to leave
aicli chat --continue                                 # pick up where you left off
aicli history -n 5 -f markdown > notes.md
aicli search quantum
aicli stats --since 7d
```

The answer goes to **stdout**. Estimates, routing decisions, cloud notices and
errors go to **stderr**. Exit codes: `0` ok, `1` a provider or batch item
failed, `2` usage or config error, `3` refused by a budget.

## Routing and budgets

```bash
aicli "Summarize this" --route cheapest --min-quality 3   # cheapest model you rated ≥ 3
aicli "Hard proof" --route best --max-cost 0.05           # best model whose worst case is ≤ $0.05
aicli "Quick" --route fastest                             # learned from your own latency history
aicli budget                                              # spend vs [budget] daily/monthly ceilings
```

## Privacy

```bash
aicli "…" --local-only            # refuse every cloud provider (or: local_only = true)
aicli audit --cloud               # every request that left this machine: host, bytes, hash
```

The audit log stores destinations, sizes and hashes, never prompt text. A
cloned repo's `.aicli.toml` is ignored until you run `aicli project trust`.
Trust is tied to the file's contents, so any change has to be trusted again.

## Templates, fragments, projects

```bash
aicli project init                                        # .aicli.toml + .aicli/templates/
git diff | aicli -t review --var language=python
aicli templates list
```

A template is Markdown with `{{vars}}`, `{{input}}` and `{{> fragment}}`.
Optional `+++` TOML front matter sets `model`, `system` and `max_tokens`. See
[`examples/templates`](examples/templates).

## Researchers: compare, batch, reproduce

```bash
aicli compare "Define entropy" -m local/llama3.2 -m haiku -m sonnet
aicli batch examples/batch/prompts.jsonl -j 8 -o results.jsonl
aicli export 42 -o runs/entropy.json      # prompt, system, temperature, seed, template
aicli rerun runs/entropy.json -m opus     # same run, different model
```

## MCP tools

```toml
[mcp.servers.files]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
allow = ["read_text_file", "list_directory"]     # deny by default
```

```bash
aicli mcp tools files
aicli "What does README.md promise?" --mcp files -m sonnet
```

Only tools in `allow` are offered to the model. Tool output is size-capped and
marked as data, not instructions. Every tool call is written to the audit log.

## CI / GitHub Actions

```yaml
- uses: warnetech/CLI-AI-Command-Center-PRD@main
  env: { ANTHROPIC_API_KEY: "${{ secrets.ANTHROPIC_API_KEY }}" }
  with:
    prompt-file: pr.diff
    model: sonnet
```

See [`examples/github-actions`](examples/github-actions) for a PR review and a
budgeted nightly batch. The action's own CI runs it end to end, offline.

## Configuration

The config is `~/.aicli/config.toml`. Set `AICLI_HOME` to use a different
directory. Keys are never stored in it: each provider names the environment
variable that holds its key. Run `aicli config show` for the fully commented
defaults.

## Development

```bash
pip install -e ".[dev]"
pytest -q            # offline: HTTP is stubbed, a fake MCP server and echo provider stand in
ruff check src tests
```

```
src/aicli/
  cli.py        commands and argument parsing
  config.py     user + trusted project config, defaults
  router.py     candidate chains, ranking, local_only, tool loop
  providers.py  Anthropic, OpenAI-compatible, Ollama, fake (HTTP via urllib)
  mcp.py        stdio MCP client and deny-by-default tool hub
  templates.py  templates, fragments, -F file context
  storage.py    SQLite history, FTS search, sessions, spend
  audit.py      JSONL audit of outbound requests
  pricing.py    price table (dated) and estimates
action.yml      GitHub Action
examples/       templates, batches, MCP and CI configs
```
