# Contributing

## Before you start

Run `make check`. If it is not green on `main`, that is the first bug.

This repository practises what it ships: read
`.claude/skills/karpathy-guidelines/SKILL.md` and `docs/07-assurance.md`, then
run `/assure` before proposing a diff.

## The bar

**Core** (`llmforge` outside `labs/`) must be correct and boring:

- no required runtime dependencies — the Anthropic SDK stays optional and lazy
- every module exercisable against `FakeProvider` with no network
- tests cover the failure path, not just the happy one
- docstrings explain *why*, not *what*; the code already says what

**`labs/`** must be falsifiable. Four answers or it does not go in: claim,
baseline, how it would fail, measurement. `/lab <name>` scaffolds it.

Nothing graduates from `labs/` to the core without an eval showing it beats the
obvious baseline. A technique that measures neutral has cost latency for
nothing.

## Two languages, one shape

Python and TypeScript mirror each other deliberately. A change to a core
concept should land in both, or say why it should not. They are allowed to
differ where the languages genuinely differ — tool schemas come from signatures
in Python and from explicit JSON Schema in TypeScript, and that is correct in
both.

## Anthropic API changes

The API moves. `packages/llmforge-python/src/llmforge/provider.py` and
`packages/llmforge-typescript/src/provider.ts` hold the version-sensitive parts —
`ADAPTIVE_THINKING_MODELS`, the `budget_tokens` stripping, streaming
thresholds. `llmforge/trace.py` holds prices with a `PRICES_AS_OF` date.

When you touch any of those, update the date and the test that pins the
behaviour. A silently stale price table is worse than no price table.

## Commits

Explain why the change was needed. The diff already shows what changed.
