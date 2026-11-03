# CLAUDE.md: aicli

This file is the map for anyone, human or AI, working in this repository, either
standalone or as `packages/aicli/` inside `warnetech/claude-code-assist`.

## Map

```
src/aicli/        the CLI: cli, config, router, providers, mcp, templates, storage, audit, pricing
tests/            pytest, fully offline (stubbed HTTP, fake MCP server in tests/fixtures/)
docs/             roadmap.md (PRD coverage), decisions.md (departures from the PRD), credentials.md
examples/         templates, fragments, batches, MCP and CI configs
action.yml        GitHub Action, tested end to end by .github/workflows/ci.yml
CLI-AI-Command-Center-PRD.md   the product plan
```

## Commands

```bash
pip install -e ".[dev]" && pytest -q && ruff check src tests
aicli "hello" -m echo/echo     # offline smoke test
```

## Rules

1. **This repository is the upstream for aicli.** claude-code-assist carries a
   git subtree copy at `packages/aicli/`. Change things here, then run
   `git subtree pull` there, so the two never drift.
2. **Standard library only** at runtime. A new dependency needs an entry in
   `docs/decisions.md` saying what it buys.
3. **Every test runs offline.** New providers get a stubbed-HTTP test, and new
   MCP behaviour gets a case in `tests/fixtures/fake_mcp_server.py`.
4. **Privacy defaults are features.** Do not loosen `local_only`, project-config
   trust, MCP deny-by-default or the audit log's never-store-prompts rule
   without a decision record.
5. **Keep `docs/roadmap.md` true.** When a gap or persona item changes status,
   update its row in the same commit.

## Glossary

- **EASECM** ("easy, cheap method"): finish the task by the simplest and
  cheapest route that still meets the requirement. That means reuse before
  building, the stdlib before a dependency, config before code. It never means
  skipping tests, safety checks or review.
