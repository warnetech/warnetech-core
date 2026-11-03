---
description: Scaffold a new frontier technique in labs/ with a falsifiable claim and a measurement plan.
---

Add a new technique to `labs/`. Argument: a short name (kebab-case) and a
one-line description of the idea.

Before writing anything, answer these four. If you cannot answer all four, the
idea is not ready for `labs/` and you should say so rather than scaffolding it:

1. **Claim** — one sentence, falsifiable. "Doing X beats Y on Z." Not "X might
   help."
2. **Baseline** — what obvious, cheaper thing this must beat. If the baseline
   is "nothing", the claim is unfalsifiable.
3. **How it would fail** — the specific mechanism, not a disclaimer. "The judge
   shares the generator's blind spots" is a mechanism; "results may vary" is
   not.
4. **Measurement** — the number that would settle it, and the number that would
   kill it.

Then create:

- `labs/NN-<name>/README.md` — the four answers, a worked example, and a
  results table (empty until someone runs it, and honest about being empty).
- `packages/llmforge-python/src/llmforge/labs/<name>.py` — a runnable implementation
  whose module docstring carries the same four answers. Export it from
  `labs/__init__.py`.
- Tests in `packages/llmforge-python/tests/` exercising it against `FakeProvider`. The
  tests must cover the failure path, not just the happy one.

Match the existing labs' shape and tone. Read `labs/README.md` first — the bar
for entry is stated there and this repository holds it.
