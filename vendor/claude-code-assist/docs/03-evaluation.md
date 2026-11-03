# Evaluation

The highest-leverage thing you can add to an LLM integration is a suite that
fails when the feature gets worse.

## Twenty of your cases beat any benchmark

A public benchmark measures a general capability. Your users do not send you a
general capability; they send the twelve things they actually ask for. Draw
cases from your bug tracker, your support queue, and the transcripts where the
feature embarrassed you.

## Variance is the whole point

```python
result = suite.run(provider, repeats=5)
print(result.report())
result.assert_at_least(0.9)
```

A prompt that passes three times in five is not a passing prompt. A single-shot
suite cannot tell the difference between that and a reliable one, which means it
will report green right up until the day it matters.

`CaseResult.flaky` is the signal to look at first — it separates "sometimes
wrong" from "consistently wrong", and those have completely different fixes.

## Gate on a rate, not on perfection

A suite that must be 100% green gets deleted the first Friday it goes red for a
reason nobody has time to fix. `assert_at_least(0.9)` survives contact with a
real team.

## Graders

Deterministic wherever one exists — cheaper, faster, more trustworthy:

```python
all_of(json_valid(SCHEMA), contains("error_code"), under_tokens(200))
```

`under_tokens` deserves a mention: verbosity is a real regression. It costs
money, buries the answer, and is the first thing that drifts when someone edits
a prompt.

Reach for `llm_judge` only when the property is real but not mechanical — "is
this explanation actually correct", not "is this valid JSON". Two rules keep a
judge honest:

1. Give it a **rubric**, not a vibe.
2. **Calibrate** it against human labels on a handful of cases before you trust
   a number it produces. An uncalibrated judge measures the judge.

## Make re-running free

```python
from llmforge import RecordingProvider

provider = RecordingProvider(AnthropicProvider(), cassette=".llmforge/evals")
```

An eval you pay for on every run is an eval you run rarely, which is an eval
that stops catching things. The cassette is keyed on the fully-shaped request,
so a one-byte prompt change correctly forces a real call. In CI, use
`replay_only=True` so a miss fails loudly instead of quietly spending money.

## Sweeps answer "can we use a cheaper model here"

```python
from llmforge import sweep

for result in sweep(suite, provider,
                    models=["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"],
                    efforts=["low", "medium", "high"], repeats=5):
    print(result.report())
```

This is how you replace a hunch with evidence — and how you find out that
`effort="medium"` was already enough for two thirds of your traffic.
