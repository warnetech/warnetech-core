# 02 · Ensemble

**Module:** `llmforge.labs.ensemble` · **Maturity:** implemented

## Claim

When verifying an answer is cheaper than producing one, spending a fixed budget
on *k samples plus selection* beats spending it all on one careful sample.

## Baseline

One sample at k× the effort, at equal dollar spend. **Not** one sample at 1×
effort — that comparison is rigged and tells you nothing.

## Why it might work

Code is the ideal case: generation is expensive and open-ended, while "do the
tests pass" is nearly free. Three selectors, increasing in cost and value:

- `self_consistency` — plurality vote. Free. Works when the answer space is
  narrow. Useless for prose, where no two samples ever match.
- `best_of_n` + **programmatic** verifier — run the tests, keep what passes.
  The version that actually earns its cost.
- `best_of_n` + **model** judge — weakest link, see below.

## How it would fail

**Correlated errors.** k samples from one model at one temperature are not k
independent draws. If the model misreads the prompt it misreads it every time,
and the ensemble converts one wrong answer into a *confident* wrong answer —
strictly worse than one wrong answer, because now you trust it.

Mitigate with `vary` (different effort, framing, or model). Treat unanimous
agreement on a hard question as suspicious rather than reassuring.

A model judge shares the generator's blind spots: it cannot see an error it
would also have made, and it will happily prefer the most confident wrong
answer.

## Measurement

| Metric | Settles it | Kills it |
|---|---|---|
| pass@1 vs. 1 sample at k× effort, equal spend | ensemble wins | ties |
| `agreement` vs. actual correctness | correlates | does not — then it is not a confidence signal |
| added p50 latency | acceptable for the win | not worth it |

## Usage

```python
from llmforge.labs import best_of_n, self_consistency

result = best_of_n(provider, prompt, score=tests_passed, k=4,
                   vary=[{"effort": "medium"}, {"effort": "high"},
                         {"model": "claude-sonnet-5"}])

vote = self_consistency(provider, question, k=5)
if vote.contested:      # no option cleared half the votes
    escalate_to_human(vote)
```

`agreement` is often more useful than `winner`: low agreement reliably flags an
ambiguous question or a guessing model, and it beats any confidence a model
states about itself.

## Results

_None yet._
