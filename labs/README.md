# labs

Techniques that are promising, implemented, and **not yet proven**.

This directory is held to a stricter standard than the core, not a looser one.
Core modules must be correct and boring. Lab modules must be **falsifiable**.

## The bar for entry

Four answers, or it does not belong here:

1. **Claim** — one sentence, falsifiable. *"Doing X beats Y on Z"*, not *"X might help"*.
2. **Baseline** — the obvious, cheaper thing it must beat. If the baseline is
   "nothing", the claim is unfalsifiable.
3. **How it would fail** — a mechanism, not a disclaimer. *"The judge shares the
   generator's blind spots"* is a mechanism; *"results may vary"* is not.
4. **Measurement** — the number that would settle it, and the number that would
   kill it.

`/lab <name>` scaffolds one.

## Graduation

Nothing graduates to the core without an eval suite showing it beats the
baseline on cost, quality, or both. A technique that sounds clever and measures
neutral has cost you latency for nothing — and most of them do.

**Nothing has graduated yet.** Saying so is the point of the directory.

## Speculative ≠ sloppy

Being unsure *whether the technique helps* is not the same as being careless
about *whether the code works*. Every lab module has tests against
`FakeProvider` covering the failure path, not just the happy one. An experiment
you cannot trust because the harness was buggy has told you nothing.

## Index

| Directory | Module | Claim |
|---|---|---|
| `01-spec-lock` | `llmforge.labs.spec_lock` | A frozen executable contract converts "looks right" into "is checked" |
| `02-ensemble` | `llmforge.labs.ensemble` | k samples + a selector beat 1 sample at equal spend |
| `03-critic` | `llmforge.labs.critic` | A separate critique pass with a fixed rubric catches what one pass misses |
| `04-ladder` | `llmforge.labs.ladder` | Escalating cheap → strong on a verifier signal costs far less at similar quality |
| `05-memory` | `llmforge.labs.memory` | Distilled lessons beat replaying whole transcripts |
| `06-redteam` | `llmforge.labs.redteam` | Adversarial framing produces runnable probes where review produces prose |

Full index with kill criteria: `docs/06-frontier-index.md`.
