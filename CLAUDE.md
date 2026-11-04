# CLAUDE.md — warnetech-core

System instructions for autonomous operation in this repository.

## Identity

This is `warnetech-core`: the shared AI gateway, memory/context layer, and
AGI capability engine for all Warnetech projects. Python 3.10+, zero required
runtime dependencies per package, pydantic for types.

## Architecture

```
src/warnetech_core/
  gateway.py         AIGateway — dispatch, retry, fallback, accounting
  registry.py        ModelSpec / ModelRegistry — aliases, pricing, fallbacks
  usage.py           UsageRecord, stores, UsageTracker, budgets
  resilience.py      RetryPolicy, TokenBucket, rate limiter registry
  config.py          Settings.from_env(), build()
  cli.py             `warnetech` command
  sync.py            SyncRunner — async gateway from synchronous code
  errors.py          Exception hierarchy
  types.py           Provider-neutral request/response types
  integrations/      make_embed_fn — warnetech_ai_controller bridge
  providers/         base, http, anthropic, openai, nvidia, ollama, lmstudio, hf_local, echo (offline)
  prompts/           PromptTemplate (versioned, strict), PromptRegistry
  memory/            types, base, inmemory, sqlite, pgvector, supabase, service
  service/           FastAPI app + schemas
  agi/               AGI capabilities — reasoning, planning, tools, reflection
  interpreter/       Natural-language code execution: workspace, executor, approval, audit
  runtime/           Host resource probe and the ollama CLI wrapper
  custom/            Custom models: spec -> Modelfile -> ollama create; dataset tools; evals
  routing/           Task classifier, model router, multi-model pipelines
core/                Standalone engine bootstrap (warnetech.sh entry point)
mcp-servers/         MCP server definitions for tool integration
scripts/             Utility and bootstrap scripts
vendor/              Bundled repos (claude-code-assist, CLI-AI-Command-Center-PRD) as git subtrees
```

## Commands

```bash
pip install -e ".[dev]"           # install with dev tools
pytest                            # 216+ tests, fully offline
ruff check src tests              # lint
WARNETECH_OFFLINE=1 python examples/quickstart.py   # run without API keys
./warnetech.sh ask "hello"        # shell wrapper
```

## Rules

1. **In-process, not HTTP.** The gateway is a library. The FastAPI service
   exists only for separately-deployed projects.
2. **One shared Supabase project.** Never create a new one. Never issue DDL
   from code — use migrations.
3. **No secrets in files.** Credentials come from environment variables.
   `.env` is gitignored. Document every var in `.env.example`.
4. **Zero required runtime deps per package.** Optional extras are imported
   lazily behind try/except or `extras_require`.
5. **Tests before merge.** Every new function with business logic gets a test.
   All tests run offline with the echo provider.
6. **AGI modules are composable.** Each capability (reasoning, planning,
   tool use, reflection) works independently and composes through the
   orchestrator. No circular imports between agi submodules.
7. **Gateway is the single exit point for model calls.** AGI modules call
   `gateway.complete()`, never a provider directly.
