# 03 · Critic

**Module:** `llmforge.labs.critic` · **Maturity:** implemented

## Claim

A separate critique pass, with a fixed rubric and a different system prompt,
finds defects the generating pass glossed over — for about two rounds.

## Baseline

One generation pass at higher effort, at equal spend.

## Why it might work

Generation and criticism pull in opposite directions. A model producing an
answer optimizes for a plausible, complete-looking artifact. The same model
asked *only* to attack an existing artifact against a fixed rubric searches for
something else.

## How it would fail

**Open-ended reflection degrades, and reports progress while doing it.** Round
one finds real bugs. Round two finds smaller ones. Round three invents problems
to justify its existence and the code gets worse.

This implementation therefore caps rounds at 2 — *that default is the finding,
not a placeholder* — requires severity-tagged structured findings so "consider
adding a comment" cannot masquerade as a defect, stops as soon as a round
produces nothing above `min_severity`, and detects oscillation between two
states.

**Also:** on tasks with a cheap ground truth (tests, a type checker, a linter)
the critic is strictly worse than running the checker. Slower, costlier, less
reliable. Use it only where no mechanical check exists — API design, error
messages, migration safety, prose.

## Measurement

| Metric | Settles it | Kills it |
|---|---|---|
| human-labelled defects, before vs. after | drops | unchanged |
| round N+1 score vs. round N | never lower | regresses |
| vs. running the linter/type-checker instead | wins where no checker exists | loses anywhere a checker exists |

## Usage

```python
from llmforge.labs import refine

result = refine(provider, task, draft=proposed_diff,
                rubric="Focus on migration safety and error messages.",
                rounds=2, min_severity="major")

print(result.stopped_because)   # why the loop ended -- read this
print(result.changed)
```

## Results

_None yet._
