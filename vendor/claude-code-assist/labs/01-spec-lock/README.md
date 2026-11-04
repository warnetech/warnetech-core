# 01 · Spec-lock

**Module:** `llmforge.labs.spec_lock` · **Maturity:** implemented

## Claim

Freezing an executable contract *before* generating code raises pass@1 on a
held-out functional test suite, because it inserts the one step the usual loop
lacks: a point where intent is written down in a form a machine can check.

## Baseline

Single-shot generation from the same request, at equal total spend.

## Why it might work

Most bad LLM code is not syntactically wrong. It is confidently wrong about
intent. Prompt → generate → eyeball → ship has no step where "looks right" is
distinguished from "is right".

The frozen step is the trick. Without it, a model that cannot satisfy a
constraint quietly rewrites the constraint, and every downstream round then
agrees with the weakened version. `IMPLEMENT_SYSTEM` forbids that and requires
a `CONTRACT-CONFLICT:` report instead — which surfaces a contradictory spec
rather than burying it.

On failure the loop feeds back **only the failing assertions**, never a
reformulated request. Restating the request is what lets a model drift.

## How it would fail

**The contract is wrong.** Then spec-lock builds the wrong thing efficiently and
confidently, and the frozen step makes it *harder* to notice. This is a real
cost and it argues for the `approve` gate, not against the technique.

Measure it by grading **contracts** against held-out human specs, not by
grading the code. If contracts are no better than the implicit spec inside a
plain prompt, the whole thing is ceremony.

## Measurement

| Metric | Settles it | Kills it |
|---|---|---|
| pass@1 on held-out functional tests | > baseline at equal spend | ties baseline |
| contract quality vs. human spec | reviewers prefer the contract | reviewers rate it no better |
| review time per accepted diff | drops | unchanged or worse |

## Usage

```python
from llmforge.labs import spec_lock

def verify(code: str, contract) -> tuple[bool, str]:
    # write code + generated assertions to a temp dir, run pytest, return failures
    ...

result = spec_lock(provider, "split a list into runs of n",
                   verifier=verify, approve=show_to_human)

print(result.contract.render())
print(result.verified, result.attempts)
if result.conflict:
    print("the contract itself is unsatisfiable:", result.conflict)
```

## Results

_None yet._ Nobody has run the measurement. The table above is what to fill in.
