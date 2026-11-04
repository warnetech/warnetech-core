# 05 · Memory

**Module:** `llmforge.labs.memory` · **Maturity:** implemented

## Claim

Distilling finished runs into short, scoped lessons beats replaying whole
transcripts: fewer steps to complete a task the agent has seen a sibling of, at
a fraction of the context cost.

## Baseline

No memory at all. (Replaying transcripts is the *bad* alternative, not the
baseline — it costs context proportional to how badly the run went.)

## Why it might work

A transcript is mostly dead weight: tool noise, retries, wrong turns. What
transfers between runs is much smaller — a handful of durable facts about *this*
codebase. *"Migrations live in db/migrate and must be reversible."* *"CI lint
rejects unsorted imports."*

A lesson is worth keeping only if it would have changed what the agent did.
`distill` drops everything else; without that filter you get a paragraph of
restated obviousness that costs context forever.

## The reinforcement model

Ported from the signature engine in `tewartech-node/claude-command-cli`, which
had already found both failure modes a naive store hits:

| Failure | Naive version | Fix |
|---|---|---|
| duplicate spawning | merge on exact string match | match on significant-word overlap; mint only when nothing matched |
| runaway confidence | linear growth | `weight += (max - weight) * rate`, flat penalty on contradiction |

`confidence` then discounts weight by the observed contradiction rate, and
decays with age — that last part is this module's addition, since an attack
signature describes a pattern that does not rot and a lesson describes a
codebase that does.

`store.saturated()` is the part worth using. A lesson that has learned all
reinforcement can teach it should be **promoted out of the store** into a
`CLAUDE.md` line, a lint rule, or a test. A lesson that has to be re-recalled
forever is one the codebase should have been made to enforce.

## How it would fail

**Lesson rot.** A distilled lesson is a snapshot of a codebase that keeps
moving. Six months on, a confident wrong lesson is worse than no memory, because
the agent trusts it. Lessons are timestamped, confirmations and contradictions
are tracked, and confidence decays — so a stale lesson is surfaced with its age
attached rather than asserted flatly.

**Memory is an injection surface.** A lesson distilled from a run that processed
a hostile README can persist an attacker's instruction into every future
session. Lessons are scanned on write and **quarantined**, not silently
dropped — you want to *see* that something tried to write an instruction into
persistent memory.

## Measurement

| Metric | Settles it | Kills it |
|---|---|---|
| steps to complete a task with a seen sibling | drops | unchanged |
| task success rate, memory on vs. off | improves | unchanged or worse |
| stale-lesson rate at 90 days | low | high enough to mislead |

## Usage

```python
from llmforge.labs import LessonStore, distill

store = LessonStore(".llmforge/lessons.json")

# at the start of a run
system = f"{BASE_SYSTEM}\n\n{store.render(scope='db/')}"

# after a run
distill(provider, transcript, source=f"run:{run_id}", store=store)
```

The store is a JSON file, not a vector database. At the scale that matters for
one repository — tens to low hundreds of lessons — reading all of them and
filtering by scope beats an embedding round trip on latency and cost, and you
can open the file and see what the agent thinks it knows. That last one is the
real argument: memory you cannot audit is memory you cannot trust.

## Results

_None yet._
