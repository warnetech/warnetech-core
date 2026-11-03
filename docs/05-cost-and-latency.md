# Cost and latency

## Measure before optimizing

```python
tracer = Tracer(sink=".llmforge/trace.jsonl")
agent = Agent(TracedProvider(provider, tracer), ...)
run = agent.run(task)
print(tracer.summary())
```

Prices in `llmforge.trace.PRICES` are a cached snapshot (`PRICES_AS_OF`). The
ledger is a smoke alarm, not an invoice. Unknown models price at Opus rates
rather than zero, because a silent `0.00` in a cost report is worse than a
conservative over-estimate.

## The levers, in order of payoff

| Lever | Typical effect | Cost |
|---|---|---|
| Prompt caching on a stable prefix | up to ~90% off the cached portion | prefix discipline |
| `effort` tuning | large; `medium` is often enough | an eval sweep to prove it |
| Better retrieval | fewer input tokens *and* better answers | the work in `docs/02` |
| Cheaper model on easy traffic | proportional to the split | a trustworthy verifier |
| Batch API | 50% off | latency you must be able to absorb |

Caching is first because it is the only one that is nearly free to adopt and
does not trade quality for money.

## Effort is not a quality dial you should max by default

Lower effort means fewer and more-consolidated tool calls, less preamble, and
terser confirmations. `high` is often the sweet spot; `max` is for when
correctness matters more than cost; `low` is right for subagents and genuinely
simple tasks. Prove it with `sweep()` rather than guessing.

## Ladders, and when they backfire

`labs/ladder` escalates cheap → strong on a verifier signal. It works when the
verifier is trustworthy in the negative direction and most traffic is easy.

Track **escalation rate** as a first-class metric. A ladder that escalates most
of the time costs *more* than going straight to the strong model, because you
paid for the failed rungs too — `savings_vs()` returns a negative number and
that is the number to watch.

## Budgets are a cost control and an availability control

```python
Budget(max_steps=15, max_seconds=300, max_usd=2.00)
```

The dollar bound is the one that saves you at 3am. An agent that loops on a
failing tool will happily spend until something stops it, and "something" should
be your budget rather than your rate limit.
