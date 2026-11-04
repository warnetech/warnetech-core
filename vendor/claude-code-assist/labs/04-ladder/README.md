# 04 · Ladder

**Module:** `llmforge.labs.ladder` · **Maturity:** implemented

## Claim

Escalating cheap → strong on a verifier signal reaches near-top-model quality at
a fraction of the spend, because the expensive rung only runs on the cases that
need it.

## Baseline

Always running the top rung.

## Why it might work

Most requests in a production workload are easy. Routing all of them to the
strongest model pays the hard-case price on every case.

## How it would fail

**Verifier miscalibration, asymmetrically.** A verifier that wrongly says
"good" silently ships the cheap model's mistake and you will not see it in
aggregate metrics. One that wrongly says "bad" merely costs money. So prefer
verifiers that fail closed — tests, compilers, schema validation — over ones
that opine.

**Distribution shift.** The mix of easy to hard cases moves, escalation rate
moves with it, and the budgeted savings evaporate. `savings_vs()` returns a
negative number when the ladder escalates too often; that is the number to
watch, and it is why escalation rate belongs on a dashboard rather than in a
footnote.

## Safety-critical note

Where an error is expensive to reverse, do not ladder — or set
`require_top_rung=True` so the strongest model signs off even when a cheaper
rung passed. You give up the savings and keep the other benefit: two
independent answers to compare. Saving four cents is not a reason to accept a
different risk profile on a path that can hurt someone.

## Measurement

| Metric | Settles it | Kills it |
|---|---|---|
| quality parity vs. always-top-model | parity | measurable drop |
| realized cost per accepted answer | materially lower | `savings_vs()` negative |
| verifier false-accept rate vs. human labels | near zero | non-trivial |

## Usage

```python
from llmforge.labs import escalate, Rung

result = escalate(provider, prompt, verify=run_tests,
                  rungs=(Rung("claude-haiku-4-5"),
                         Rung("claude-sonnet-5", effort="medium"),
                         Rung("claude-opus-5", effort="high")))

print(result.accepted, result.settled_on, result.escalated)
```

`accepted=False` means nothing verified. The text returned is the top rung's
attempt, flagged — returning a rejected answer as if it passed is the failure
mode this module exists to prevent.

## Results

_None yet._
