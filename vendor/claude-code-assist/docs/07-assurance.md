# Assurance: turning coding principles into controls

> Prose guidance works most of the time and silently not the rest of the time.
> Where a mistake is expensive to reverse, "most of the time" is not a control.

## The problem with good advice

The [Karpathy guidelines](https://github.com/multica-ai/andrej-karpathy-skills)
are four rules that measurably reduce LLM coding mistakes:

1. **Think Before Coding** — don't assume; surface confusion and tradeoffs
2. **Simplicity First** — minimum code that solves the problem
3. **Surgical Changes** — touch only what the request requires
4. **Goal-Driven Execution** — define success criteria; loop until verified

They are correct, and putting them in a `CLAUDE.md` genuinely helps. But they
share the limitation of all prompt guidance: **compliance is unobservable**. You
cannot tell from a diff whether the agent followed rule 3 or merely read it. The
failure is silent, and silent failures are the ones that reach production.

That is fine when the worst case is a wasted turn. It is not fine when the
worst case is a change that someone will later be asked to account for.

## The move

Each principle becomes a check with a verdict.

| Principle | Gate | What it can actually decide |
|---|---|---|
| Think Before Coding | `AssumptionLedger` | Were assumptions declared? Is each supported by evidence? Are there unanswered questions? |
| Simplicity First | `complexity_budget` | How much was added, against a declared budget and against the code being replaced? Are there shapes that indicate speculative generality? |
| Surgical Changes | `diff_discipline` | Does every hunk trace to the request? Is any hunk pure reformatting? Was a comment deleted without replacement? |
| Goal-Driven Execution | `SuccessCriteria` | Is there a criterion at all? Is it phrased as an outcome rather than an activity? Does a verifier exist, and does it pass? |

None of these call a model. They are static analysis over text you already
have — fast, free, deterministic, and structurally incapable of hallucinating.
Those are exactly the properties you want in the component whose job is to say
*no*.

## Why each gate draws the line where it does

### An empty ledger is a blocker, not a warning

No real request is fully specified. An agent that declared zero assumptions did
not fail to have any — it failed to look. The gate is therefore not "are your
assumptions good" (unanswerable) but "did you do the looking" (answerable), and
the empty case is the one that matters.

Genuine one-liners are exempt via `trivial=True`. The upstream guidelines make
the same carve-out, and a gate that fires on typo fixes gets disabled within a
week.

### Growth against a baseline beats an absolute line count

"If you write 200 lines and it could be 50, rewrite it" cannot be checked
mechanically — *could be* is a judgment. But the signal that correlates best
with it is available: **how big is this compared to what it replaces?** A
rewrite four times the size of the code it replaces is nearly always solving
problems nobody asked about. That is why `baseline_lines` is the strongest
input to `complexity_budget` and an absolute `max_lines` is the weak fallback.

The speculative-generality patterns (`AbstractHandlerBase`, `makeThingFactory`,
an empty `catch`) are `note` severity, never blocking. They have real false
positive rates, and a gate that blocks on a naming convention teaches people to
route around it.

### Deleted comments block; drive-by formatting only warns

This asymmetry is deliberate. Reformatting costs review attention — annoying,
recoverable. Deleting a comment destroys information that exists nowhere else:

```diff
-# leeway is 60s because the auth server clock drifts; see INC-2231
```

The code cannot carry that. Once the line is gone, the next person to "simplify"
the leeway to zero has no way to know why it was there, and the incident
recurs. This is the specific failure the upstream guidelines call out — *"they
change or remove comments and code they don't sufficiently understand"* — and
it is the one with the longest tail, so it is the one that blocks.

`diff_discipline` distinguishes a comment that was **moved** from one that was
**deleted**, so refactors that relocate a comment pass cleanly.

### A criterion you cannot fail is not a criterion

`SuccessCriteria.check()` rejects "make it work", "fix it", "refactor",
"improve". These describe an activity, not an outcome. An agent handed one of
them cannot know when to stop, so it stops when it runs out of budget or when
it feels finished — neither of which is a result.

The transformation the guidelines prescribe is exactly right, and the gate just
insists on it:

| Instead of | Require |
|---|---|
| "add validation" | "tests for invalid inputs pass" |
| "fix the bug" | "the test reproducing it passes" |
| "refactor X" | "the suite passes before and after" |

`gate_agent` makes this binding: an agent wrapped with success criteria that do
not pass pre-flight **will not start**. Deny-by-default, applied to autonomy
itself.

## The pair

```python
from llmforge.assurance import AssumptionLedger, Postflight, Preflight, SuccessCriteria

# --- before any code exists -------------------------------------------------
ledger = AssumptionLedger("add retry to the uploader")
ledger.assume("retries are safe", because="upload() PUTs to a content-addressed key")
ledger.rejected("exponential backoff in the caller", "three call sites would each need it")

goals = (
    SuccessCriteria("add retry to the uploader")
    .require("test_upload_retries_on_503 passes", run_pytest)
    .step("write the failing test", "it fails for the right reason")
    .step("add retry", "the test passes")
)

Preflight(ledger=ledger, goals=goals).run().raise_if_blocked()

# --- after the change exists ------------------------------------------------
Postflight(
    diff=subprocess.run(["git", "diff"], capture_output=True, text=True).stdout,
    request_terms=["upload", "retry"],
    allowed_paths=["src/upload/"],
    goals=goals,
    baseline_lines=18,
).run().raise_if_blocked()
```

Pre-flight is the cheap moment. An assumption caught there costs one
conversation turn; the same assumption caught in review costs a day; caught in
production it costs whatever it costs.

## What running them on themselves found

The first CI run of the assurance job against this repository's own diff
produced about thirty findings, all false positives. Three defects, none of
which any unit test would have caught:

**`unused-config-knob` was removed, not tuned.** It claimed to flag *"a
configuration point added without a second caller"*. Whether a config point has
a second caller is a cross-file fact, and a single-file regex cannot see
callers — it never could do what it claimed. In practice it fired on every
`model:` and `options:` in the repository, because `model` contains `mode` and
`options` contains `option`. Detecting this properly needs call-graph analysis,
not a pattern table.

**Nesting was measured on indentation, which is a bad proxy.** A wrapped call
argument or a multi-line dict is indented four, eight, sixteen spaces without
nesting anything. `guard.py`, whose real nesting is 3, reported 8. Depth is now
counted on lines that actually open a block, and the indent unit is inferred
rather than assumed.

**The CI job concatenated the whole diff into one blob.** Line numbers
referenced a synthetic file (*line 13643*), and the fixtures and the
deliberately-bad example were scanned as if they were product code — so the
gates reported their own test material as defects. It now scans per file and
skips paths whose entire purpose is to contain bad patterns.

After all three, the same diff reports **0 blocking, 6 advisory** — six real
nesting warnings on genuinely nested code.

`test_our_own_source_passes_the_gates_we_ship` now runs every gate over every
module in this package on every test run, so a check that starts firing on its
own repository fails CI. That regression test is the actual fix; the three
above are what it would have caught.

The general lesson, and it is the one this whole document is about: **a check
with a bad false-positive rate is worse than no check.** It still occupies the
slot, and people learn to skip the output. Precision matters more than recall
for anything advisory.

## What these gates cannot do

Being explicit about this is load-bearing, because a green verdict that implies
more assurance than it earned is worse than no verdict at all.

They **cannot** tell you:

- whether the code is **correct**. Nothing here executes anything. That is what
  `SuccessCriteria` verifiers are for, and they are only as good as your tests.
- whether the change is the **right change**. A perfectly surgical, minimal,
  fully-verified implementation of the wrong feature passes every gate.
- whether an assumption is **true**. The ledger checks that you declared one and
  cited something. It cannot check that the citation supports the claim.
- anything about **performance, security, or accessibility**. Different tools.

They also produce false positives. A file genuinely required by a request but
not named in it will warn; a comment correctly deleted because it was wrong will
block. Both are meant to be **argued with**, not suppressed. A false positive
you silence quietly is how a gate stops being believed, and a gate nobody
believes is worse than no gate — it provides cover.

## Fail-safe by default

The gates compose with the authority controls in `llmforge.audit`:

- `SafePolicy` — nothing runs unless explicitly allowed. New tools arrive
  denied, which is noisy the first time and correct every time.
- `DualControl` — the two-person rule for anything irreversible. One approver
  can be tired, socially engineered, or the agent wearing a convincing hat.
- `AuditLog` — hash-chained, append-only, secrets redacted on write. Each entry
  commits to its predecessor, so an edit or deletion is localized to a sequence
  number.

The honest limit on the audit chain: it proves **internal consistency**.
Whoever can rewrite the file can rewrite the chain. Real tamper-evidence needs
an external anchor — publish `log.head` somewhere the agent cannot reach and
compare. The library makes that cheap and cannot do it for you.

## Further reading

- The upstream guidelines: `.claude/skills/karpathy-guidelines/SKILL.md`
- Attribution and license: `THIRD_PARTY_NOTICES.md`
- The gates: `packages/llmforge-python/src/llmforge/assurance.py`, `packages/llmforge-typescript/src/assurance.ts`
- Their tests, which are the real specification:
  `packages/llmforge-python/tests/test_assurance.py`
