# Frontier index

Every entry in `labs/` states a claim, ships a runnable implementation, names
how it would fail, and specifies the measurement that would settle it.

**Maturity** is honest, not aspirational:

- **implemented** — the code works and is tested; nobody has run the measurement
- **measured** — someone ran it and the numbers are in the lab's README
- **graduated** — it beat the baseline and moved into the core

Nothing is graduated yet. Saying so is the point.

| Lab | Claim | Maturity | Kills it |
|---|---|---|---|
| `spec_lock` | Freezing an executable contract before writing code converts "looks right" into "is checked" | implemented | contract quality is no better than a plain prompt's implicit spec |
| `ensemble` | k samples + a selector beat 1 sample at equal spend, when verifying is cheaper than generating | implemented | pass@1 ties a single sample at k× effort |
| `critic` | A separate critique pass with a fixed rubric catches defects a single pass misses | implemented | round 2 lowers the score of round 1 |
| `ladder` | Escalating cheap → strong on a verifier signal costs far less at similar quality | implemented | escalation rate high enough that `savings_vs` goes negative |
| `memory` | Distilled lessons beat replaying whole transcripts | implemented | step count does not drop on tasks with a seen sibling |
| `redteam` | Adversarial framing produces runnable probes where review produces prose | implemented | probes reproduce no more real defects than a review at equal spend |

## The bar for entry

Four answers, or it does not belong:

1. **Claim** — one sentence, falsifiable. "Doing X beats Y on Z", not "X might help".
2. **Baseline** — the obvious, cheaper thing it must beat. If the baseline is
   "nothing", the claim is unfalsifiable.
3. **How it would fail** — a mechanism, not a disclaimer. "The judge shares the
   generator's blind spots" is a mechanism; "results may vary" is not.
4. **Measurement** — the number that would settle it, and the number that would
   kill it.

Use `/lab <name>` to scaffold one.

## Why "mostly frontier" still means "fully tested"

Speculative about *whether the technique helps* is not the same as sloppy about
*whether the code works*. Every lab module has tests against `FakeProvider`,
covering the failure path and not just the happy one. An experiment whose result
you cannot trust because the harness was buggy has told you nothing.
