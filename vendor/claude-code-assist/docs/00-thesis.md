# Thesis

Most "LLM integration" in codebases today is a call to an API wrapped in
optimism. It works in the demo, works in review, and then fails in the specific
way that nobody instrumented for.

This repository is a bet on four claims.

## 1. The model is the least interesting part

Swapping models is a one-line change and rarely the thing that moves quality.
What moves quality, in rough order of leverage:

1. **What went into the context.** A weaker model with the right three files
   beats a stronger model with the wrong thirty, and costs a tenth as much.
2. **Whether you can tell if it got worse.** Twenty cases from your own bug
   tracker beat any public benchmark, because they encode what your users
   actually asked for.
3. **What happens when it's wrong.** Recoverable-by-design beats accurate-on-average.
4. **Which model.**

Almost all the code here is about 1 through 3.

## 2. Advice that cannot fail is not a control

The industry's answer to LLM coding mistakes is prompt guidance: put the rules
in a `CLAUDE.md` and the model will follow them. It largely works — and its
compliance is unobservable. You cannot tell from a diff whether the agent
followed "touch only what you must" or merely read it.

Where a mistake is cheap, that trade is right. Where it is expensive to
reverse, it is not, and the fix is to compile the guidance into checks that
return a verdict. `docs/07-assurance.md` does this for four principles that
have earned their place. It is the part of this repository we would defend
hardest.

## 3. Bounded beats capable

An agent with no ceiling is an outage waiting for the right prompt. Every loop
here carries a budget in steps, seconds, and dollars; every tool surface can be
deny-by-default; every irreversible action can require two people; every run
can leave a tamper-evident trail.

None of this makes the agent smarter. All of it makes the agent *deployable* —
and an agent you will actually let near production beats a cleverer one you
won't.

## 4. Frontier techniques deserve falsifiable claims, not enthusiasm

`labs/` is where the speculative work lives, and it is held to a stricter
standard than the core, not a looser one. Every entry states a claim, ships a
runnable implementation, names the mechanism by which it would fail, and
specifies the measurement that would settle it.

Nothing graduates to the core without an eval showing it beats the obvious
baseline on cost, quality, or both. A technique that sounds clever and measures
neutral has cost you latency for nothing — and most of them do. Saying so is
the point of the directory.

## The shape that follows

```
provider   one seam to the vendor — real, fake, record/replay, retrying
trace      spans and a cost ledger, because unobservable is unimprovable
tools      typed tools, generated schemas, policy-gated dispatch
loop       a bounded loop that handles refusal and pause_turn correctly
context    walk -> rank -> pack a repository into a token budget
guard      untrusted-content envelopes, secret redaction, output validation
evals      a variance-aware suite you can gate CI on
assurance  four coding principles compiled into gates that can fail
audit      hash-chained trail, deny-by-default authority, dual control
labs       frontier techniques, each with a falsifiable claim
```

Two implementations — Python and TypeScript — with the same shape, because the
ideas are not language-specific and neither is the failure mode.

## What this is not

- **Not a framework.** There is no runtime to adopt, no config format, no
  plugin system. Import what helps; the modules do not know about each other
  beyond the provider seam.
- **Not a benchmark suite.** The eval harness is for *your* cases.
- **Not a safety guarantee.** The guardrails raise the cost of an attack and
  make failures visible. A competent adversary defeats every heuristic here.
  Read `docs/04-guardrails.md` for what each one actually buys.
