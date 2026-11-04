# 06 · Red team

**Module:** `llmforge.labs.redteam` · **Maturity:** implemented

## Claim

Adversarial framing produces *runnable probes* where a review pass produces
prose, and the probes reproduce more real defects at equal spend.

## Baseline

A plain review pass ("is this code correct?") at equal spend.

## Why it might work

"Is this correct?" gets you a review. "You are trying to break this, and you get
one input" gets you a test case. The framing changes what the model searches
for, and the output is concrete enough to run — the difference between a review
comment and a regression test.

Two modes, because two different things go wrong:

- `attack_code` — inputs that violate a stated contract. Pairs naturally with
  `01-spec-lock`: the frozen contract is exactly the document this needs, and
  the probes become the next round's failing assertions.
- `probe_prompt` — inputs that make an *agent* exceed its authority. The one
  that matters when the agent can touch anything real, and how you find out that
  "never delete without confirmation" was a suggestion.

## How it would fail

**Shared blind spots.** A model red-teaming itself cannot generate the attack it
would not have thought to defend against. This finds the shallow half of the
problem cheaply. It complements fuzzing, property testing, and an actual
adversary — it does not substitute for them, and it must never be the last gate
in front of something that can hurt someone.

**Crying wolf.** A red team with a high false-positive rate gets ignored, which
is worse than not running it.

## Measurement

| Metric | Settles it | Kills it |
|---|---|---|
| probes reproducing a real defect vs. plain review, equal spend | more | same or fewer |
| false-positive rate | low enough to keep reading | high enough that people stop |
| authority-boundary probes caught by the harness | all of them | any that succeed |

## Usage

```python
from llmforge.labs import attack_code, probe_prompt

# against an implementation
result = attack_code(provider, code, contract.render())
if not result.clean:
    print(result.report())

# against your own agent -- keep these as a regression suite
probes = probe_prompt(provider, SYSTEM_PROMPT, tools.definitions())
for case in probes.as_cases():
    assert harness_refuses(case["input"]), case["expect_violation"]
```

The value is not the first run. It is that six months later, when someone widens
a tool's schema, the suite tells you the boundary moved.

## Results

_None yet._
