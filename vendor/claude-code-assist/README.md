# claude-code-assist

**Wiring LLMs into real codebases** — an agent loop that handles the awkward
cases, context packing, guardrails, a variance-aware eval harness, a
tamper-evident audit trail, and a lab of frontier techniques that each state
what would prove them wrong.

Two implementations with the same shape: **Python** (`llmforge`) and
**TypeScript** (`@llmforge/core`). Zero required runtime dependencies in both —
the Anthropic SDK is optional and lazily imported, so the entire library, its
tests, and its examples run offline against a scripted provider.

```bash
make install && make check     # every test in all three packages, offline, no credentials
llmforge doctor                # self-test: reports rather than raises
python examples/02_gate_catches_a_drive_by.py
```

---

## The idea

Most LLM integration is an API call wrapped in optimism. It works in the demo,
works in review, and then fails in the specific way nobody instrumented for.

Four bets, argued in [`docs/00-thesis.md`](docs/00-thesis.md):

1. **The model is the least interesting part.** What went into the context,
   whether you can tell if it got worse, and what happens when it's wrong all
   outrank which model you picked.
2. **Advice that cannot fail is not a control.** Prompt guidance works most of
   the time and silently not the rest. Where a mistake is expensive to reverse,
   compile the guidance into checks that return a verdict.
3. **Bounded beats capable.** An agent you will actually let near production
   beats a cleverer one you won't.
4. **Frontier techniques deserve falsifiable claims, not enthusiasm.**

---

## The part that is new: assurance gates

The [Karpathy guidelines](https://github.com/multica-ai/andrej-karpathy-skills)
are four rules that measurably reduce LLM coding mistakes. They ship as prose in
a `CLAUDE.md`, and as prose their compliance is **unobservable** — you cannot
tell from a diff whether the agent followed *"touch only what you must"* or
merely read it.

This repository vendors them verbatim ([with attribution](THIRD_PARTY_NOTICES.md))
**and compiles each one into a gate that can fail a run**:

| Principle | Gate | Decides |
|---|---|---|
| Think Before Coding | `AssumptionLedger` | Were assumptions declared, and is each supported? |
| Simplicity First | `complexity_budget` | How much was added, against a budget and against what it replaced? |
| Surgical Changes | `diff_discipline` | Does every hunk trace to the request? Was a comment deleted? |
| Goal-Driven Execution | `SuccessCriteria` | Is there a criterion, is it an outcome, does it pass? |

None of them call a model. They are static analysis over text you already
have — fast, free, deterministic, and structurally incapable of hallucinating.
Those are the properties you want in the component whose job is to say *no*.

Both languages implement the same gates, and `fixtures/assurance-parity.json`
pins them to identical behaviour — 30 cases asserted by both test suites, on
machine-readable violation codes rather than prose. Two implementations of one
contract drift; [`docs/08-operations.md`](docs/08-operations.md#the-parity-fixtures)
records the six real divergences the fixtures caught on their first run.

```python
from llmforge.assurance import AssumptionLedger, Preflight, SuccessCriteria

ledger = AssumptionLedger("add retry to the uploader")
ledger.assume("retries are safe", because="upload() PUTs to a content-addressed key")

goals = SuccessCriteria("add retry").require("test_upload_retries passes", run_pytest)

Preflight(ledger=ledger, goals=goals).run().raise_if_blocked()
```

Run `python examples/02_gate_catches_a_drive_by.py` to watch all four fire on a
diff that quietly reformatted an unrelated file and deleted a comment it did not
understand. Reasoning behind every threshold: [`docs/07-assurance.md`](docs/07-assurance.md).

---

## Quick tour

```python
from llmforge import Agent, Budget, ToolRegistry, Tracer, TracedProvider, tool

@tool(parallel_safe=True)
def grep(pattern: str, path: str = ".") -> str:
    """Search the repository for a regex.

    Args:
        pattern: The regular expression to search for.
        path: Directory to search under.
    """
    ...

tracer = Tracer(sink=".llmforge/trace.jsonl")
agent = Agent(
    TracedProvider(provider, tracer),
    tools=ToolRegistry(grep),
    budget=Budget(max_steps=15, max_usd=2.00),   # finite by default
    effort="high",
)
run = agent.run("Find why the uploader retries twice on 503.")
print(tracer.summary())   # spans, tokens, cache hit rate, dollars
```

The tool schema comes from the signature and the docstring, so it cannot drift
from the implementation.

### What the loop gets right that a naive one doesn't

- **`stop_reason == "refusal"` is an HTTP 200.** Code that reads `content`
  without checking gets an empty string and no error.
- **`pause_turn` needs resuming.** The SDK tool runner returns a paused turn as
  if it were final — a silently truncated answer.
- **All `tool_result` blocks go back in one message.** Splitting them teaches
  the model to stop calling tools in parallel.
- **`budget_tokens` is a 400 on current models.** Stripped at the provider seam.
- **Budgets bind.** Steps, wall-clock, and dollars, checked every turn.

---

## Modules

| Module | What it does |
|---|---|
| `provider` | one seam to the vendor — real, fake, record/replay, retrying |
| `trace` | spans and a cost ledger; unobservable is unimprovable |
| `tools` | typed tools, generated schemas, policy-gated dispatch |
| `loop` | a bounded agent loop that handles the awkward stop reasons |
| `context` | walk → rank → pack a repository into a token budget |
| `guard` | untrusted-content envelopes, secret redaction, output validation |
| `evals` | a variance-aware suite you can gate CI on |
| `assurance` | four coding principles compiled into gates that can fail |
| `audit` | hash-chained trail, deny-by-default authority, dual control |
| `limits` | a token bucket in front of the provider |
| `retention` | tiered pruning for traces and audit chains |
| `doctor` | a self-test that reports rather than raises |
| `labs` | frontier techniques, each with a falsifiable claim |

### Fail-safe defaults

```python
from llmforge.audit import AuditLog, DualControl, SafePolicy

log = AuditLog(".llmforge/audit.jsonl")           # hash-chained, redacts on write
control = DualControl(log, approvers={"alice", "bob"})   # two-person rule
policy = SafePolicy(log, allow={"grep", "read_file"}, dual_control=control)
```

Authority is granted, never inherited: a tool that appears through a config
change, a merge, or a new MCP server arrives **denied**. `log.verify()` localizes
any edit or deletion to a sequence number.

The honest limit: a hash chain proves *internal* consistency. Whoever can
rewrite the file can rewrite the chain. Publish `log.head` somewhere the agent
cannot reach — that is what turns it into real tamper-evidence.

---

## labs/

Six techniques, each with a claim, a runnable implementation, the mechanism by
which it would fail, and the measurement that would settle it.

| Lab | Claim | Kills it |
|---|---|---|
| [spec-lock](labs/01-spec-lock) | A frozen executable contract converts "looks right" into "is checked" | contracts no better than a plain prompt's implicit spec |
| [ensemble](labs/02-ensemble) | k samples + a selector beat 1 sample at equal spend | ties one sample at k× effort |
| [critic](labs/03-critic) | A critique pass with a fixed rubric catches what one pass misses | round 2 lowers round 1's score |
| [ladder](labs/04-ladder) | Escalating cheap → strong costs far less at similar quality | escalation rate makes `savings_vs` negative |
| [memory](labs/05-memory) | Distilled lessons beat replaying transcripts | step count doesn't drop |
| [redteam](labs/06-redteam) | Adversarial framing yields runnable probes, not prose | no more real defects than a review |

**Nothing has graduated to the core yet.** Saying so is the point. Speculative
about *whether a technique helps* is not the same as sloppy about *whether the
code works* — every lab module is tested, failure paths included.

---

## Documentation

| | |
|---|---|
| [00 · Thesis](docs/00-thesis.md) | what this bets on, and what it is not |
| [01 · Patterns](docs/01-patterns.md) | the ladder from one call to an agent |
| [02 · Context engineering](docs/02-context-engineering.md) | ranking, packing, and why your cache isn't hitting |
| [03 · Evaluation](docs/03-evaluation.md) | variance, judges, and making re-runs free |
| [04 · Guardrails](docs/04-guardrails.md) | what each control buys — and what it doesn't |
| [05 · Cost and latency](docs/05-cost-and-latency.md) | the levers, in order of payoff |
| [06 · Frontier index](docs/06-frontier-index.md) | every lab's kill criterion |
| [07 · Assurance](docs/07-assurance.md) | turning coding principles into controls |
| [08 · Operations](docs/08-operations.md) | rate limits, `doctor`, retention, and where they came from |
| [09 · Credentials](docs/09-credentials.md) | every key and service, which are optional, and a checklist to fill in |

## For Claude Code sessions

`.claude/` ships two skills and two commands:

- **`karpathy-guidelines`** — the vendored four principles
- **`llm-integration`** — which primitive to reach for, plus the API facts that
  are easy to get wrong
- **`/assure`** — run the gates against your working tree
- **`/lab <name>`** — scaffold a new technique with a falsifiable claim

## aicli: the command center

[`packages/aicli`](packages/aicli) is the CLI-to-AI Command Center, merged in
from `warnetech/CLI-AI-Command-Center-PRD` as a git subtree. It is a
local-first CLI for Claude, OpenAI-compatible APIs and Ollama, with:
- cost-, latency- and quality-aware routing
- budgets and an audit log of what leaves the machine
- MCP tools (deny by default)
- templates and resumable chats
- `compare`, `batch` and `rerun`
- a GitHub Action

It has no runtime dependencies. Coverage of
[its PRD](packages/aicli/CLI-AI-Command-Center-PRD.md), feature by feature and
persona by persona, is in [`packages/aicli/docs/roadmap.md`](packages/aicli/docs/roadmap.md).

```bash
cd packages/aicli && pip install -e ".[dev]"
aicli "hello" -m echo/echo      # offline echo provider
aicli list providers
```

## Layout

```
packages/llmforge-python/src/llmforge/   llmforge (Python): provider, tools, loop, context,
                                         guard, evals, assurance, audit, labs/
packages/llmforge-typescript/src/        llmforge (TypeScript, @llmforge/core): the same core
packages/aicli/                          aicli: multi-model command-center CLI (git subtree)
fixtures/                                parity cases both llmforge implementations must pass
labs/                                    claim · protocol · failure mode · measurement
docs/                                    the reasoning behind all of it, read in number order
examples/                                run offline, no credentials
scripts/                                 repo checks (layout contract)
```

## License

MIT. Third-party attributions in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
