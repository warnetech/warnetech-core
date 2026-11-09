# Warnetech Core AGI

Warnetech Core contains an evolving AGI orchestration layer built around planning, tool execution, reflection, confidence scoring, capability metadata and telemetry.

The AGI implementation lives under:

```text
src/warnetech_core/agi/
```

This is the canonical AGI package. New AGI functionality should extend the existing implementation rather than introduce a parallel root-level `agi/` package.

---

## Status

### Phase 1

**Status:** CLOSED

| Task | Description | Status |
|---|---|---|
| T1 | Planner duplicate-title dependency handling | ✅ Complete |
| T2 | Stable `Goal.id` | ✅ Complete |
| T3 | Capability metadata | ✅ Complete |
| T4 | Confidence framework | ✅ Complete |
| T5 | Telemetry | ✅ Complete |
| T6 | Integration | ✅ Complete |
| T7 | Documentation and final validation | ✅ Complete |

### Phase 2

**Status:** In progress. Plan: `docs/plans/2026-11-09-phase-2/plan.md`.

Built, not yet wired into `AGIOrchestrator`:

- `agi/state.py`: `AgentStateStore` (goal history) and `ReflectionHistory`.
- `agi/tool_metrics.py`: `ToolMetrics` with Beta-posterior ranking and Thompson-sampling selection.

---

# Phase 1

## T1: Planner Dependency Handling

The planner correctly handles duplicate task titles during dependency resolution.

The implementation is located in:

```text
src/warnetech_core/agi/planning.py
```

The relevant logic is implemented in:

```python
_parse_tasks
```

A task title may correspond to multiple task IDs.

When another task declares a dependency using that repeated title, the dependency resolves to **all matching task IDs** rather than an arbitrary single match.

This ensures the dependent task waits for every task matching the referenced title.

Regression coverage includes:

```python
test_duplicate_title_dependency_waits_for_every_match
```

in:

```text
tests/test_agi_planning.py
```

---

## T2: Stable Goal Identity

Goals have stable identifiers through:

```python
Goal.id
```

The identifier provides a consistent correlation key across AGI execution.

Conceptually:

```text
Goal
├── id
└── description
```

The goal ID can be associated with:

- planning;
- execution;
- confidence results;
- reflection;
- telemetry;
- future persistent state.

The human-readable goal description should not be used as the primary identity of a goal.

---

## T3: Capability Metadata

Phase 1 introduces a metadata-only capability registry.

Capability metadata can describe:

```text
Capability
├── name
├── description
├── version
└── dependencies
```

`ToolDefinition` also supports:

```python
version
dependencies
```

with backwards-compatible defaults.

### Important Runtime Limitation

The capability registry is currently **stored but not read by the main execution loop**.

It therefore provides capability metadata but does not currently perform autonomous capability selection.

Do not describe the current runtime as:

```text
Planner
    ↓
Capability Registry
    ↓
Tool Selector
```

unless future implementation work actually connects those components in that manner.

The documentation must describe the implemented system, not the intended future architecture.

---

## T4: Confidence Framework

Confidence scoring is implemented in:

```text
src/warnetech_core/agi/confidence.py
```

with tests in:

```text
tests/test_agi_confidence.py
```

Confidence values use the range:

```text
0.0 - 1.0
```

The framework is designed so that:

- additional supporting evidence can increase confidence;
- contradictions can reduce confidence;
- values remain bounded between `0.0` and `1.0`;
- scoring remains deterministic.

### Current Runtime Behaviour

Confidence is calculated **once at the end of a run**.

It is not currently an intermediate routing mechanism between execution and reflection.

Future architecture diagrams must preserve this distinction until the runtime behaviour changes.

---

## T5: Telemetry

Telemetry is implemented in:

```text
src/warnetech_core/agi/telemetry.py
```

with tests in:

```text
tests/test_agi_telemetry.py
```

Phase 1 uses JSONL telemetry.

### Canonical Events

The Phase 1 telemetry vocabulary is:

```text
goal_received
plan_generated
tool_selected
execution_completed
reflection_completed
```

Alternative names such as:

```text
goal_started
goal_completed
```

should not be introduced independently.

A single vocabulary keeps event producers and consumers consistent.

Where appropriate, events should include:

```text
goal_id
timestamp
event
metadata
```

so activity from one execution can be correlated.

---

## Telemetry Import Path

Telemetry is not currently re-exported from the AGI package's public package surface.

Import it from its module:

```python
from warnetech_core.agi.telemetry import Telemetry, TelemetryEvent
```

The public API should not be expanded solely to make an incorrect documentation example work.

The rule is:

> Documentation describes the API that exists. The API is not changed simply to accommodate documentation that described something else.

---

## T6: Integration

Phase 1 components are integrated with the existing AGI implementation.

Two implementation details are particularly important.

### Capability Metadata

The capability registry exists, but the current execution loop does **not** use it for autonomous capability selection.

### Confidence

Confidence is computed once at run completion rather than being used as an intermediate orchestration decision.

The source code remains authoritative for the precise execution order.

Future documentation should not imply functionality that has not yet been wired into the runtime.

---

# Phase 1 Boundaries

Phase 1 deliberately excludes persistent AGI state.

Reflection information remains in memory.

Phase 1 does **not** introduce:

```text
persistent reflection history
persistent goal history
tool performance history
long-term agent state
adaptive learning state
```

These responsibilities belong to Phase 2.

This boundary keeps Phase 1 focused on execution infrastructure rather than mixing execution with persistence and learning.

---

# Repository State Management

The `.clawness` directory contains both runtime state and durable records.

These must remain separate.

## Runtime State

The following files are runtime/session state and are git-ignored:

```text
.clawness/sessions.json
.clawness/guard_sessions.json
.clawness/handoff.md
.clawness/handoff_autostart.json
```

## Durable Records

Other `.clawness` files are project records and remain tracked:

```text
.clawness/memory.md
.clawness/changelog.json
.clawness/version.json
.clawness/sast.json
```

Do **not** use broad ignore rules such as:

```gitignore
.clawness/*.json
.clawness/*.md
```

because those patterns would also exclude durable records.

---

# Performance Optimisation

Automated optimisation is not part of Phase 1.

Tools that rewrite code for performance should not be run merely because AGI files have recently changed.

Phase 1 prioritises:

1. correctness;
2. deterministic behaviour;
3. maintainability;
4. test coverage;
5. architectural consistency.

Optimisation should be performed when a measurable performance issue exists and appropriate benchmarks can verify that behaviour remains correct.

---

# Phase 2

The existing roadmap (`docs/agi-roadmap.md`) remains the source of truth.

Its Phase 2 structure is:

```text
Phase 2
├── Reflection Engine
├── Persistent State
└── Tool Selection
```

Additional ideas are implementation tasks beneath these areas rather than a competing roadmap.

---

## Reflection Engine

```text
Reflection Engine
├── Reflection improvements
├── Reflection history      (ReflectionHistory built)
├── Outcome analysis
└── Retry recommendations
```

The existing:

```text
src/warnetech_core/agi/reflection.py
```

should be extended rather than replaced with a parallel reflector implementation.

---

## Persistent State

```text
Persistent State
├── State store             (AgentStateStore built)
├── Goal history            (AgentStateStore built)
├── Reflection persistence
└── Restart recovery
```

Persistence has an explicit schema and lifecycle rather than serialising arbitrary runtime objects.

Telemetry and persistent state remain separate responsibilities.

---

## Tool Selection

```text
Tool Selection
├── Tool metrics            (ToolMetrics built)
├── Cold-start prior        (Beta(1, 1): untried tool = 0.50)
├── Tool ranking            (posterior-mean ranking built)
└── Adaptive selection      (Thompson sampling built)
```

### Cold-Start Prior

A naïve success rate (`successes / executions`) is undefined for an untried tool. Scoring it as `0.0` would make a new tool look like a consistently failing one.

The implementation uses a Beta prior. With the default `Beta(1, 1)`, the posterior mean is `(successes + 1) / (trials + 2)`:

```text
0 / 0   -> 0.50
1 / 1   -> 0.67
10 / 10 -> 0.92
0 / 10  -> 0.08
```

### Not Yet Settled

- The ranking uses success rate only. Latency, confidence and capability compatibility are not yet combined into the score.
- Thompson sampling is not yet wired into the orchestrator.

---

# Architecture Principles

## Extend Before Adding

Before creating a new AGI component, determine whether the functionality belongs in an existing module.

Avoid unnecessary parallel implementations of:

```text
planners
tool registries
reflectors
orchestrators
state systems
```

---

## Code Is Authoritative

Documentation describes the running implementation.

Future architecture should be explicitly labelled as future architecture.

---

## Separation of Concerns

```text
Planning
    → creates plans

Tools
    → execute operations

Capability Metadata
    → describes capabilities

Confidence
    → quantifies uncertainty

Reflection
    → evaluates outcomes

Telemetry
    → records observable events

Persistent State
    → stores durable AGI state
```

These responsibilities should remain separate unless there is a strong architectural reason to combine them.

---

## Prefer Minimal Changes

Extend stable components before replacing them.

Prefer small, reviewable changes over large architecture rewrites.

---

## Test Behaviour

Regression tests should protect externally meaningful behaviour while allowing implementation details to evolve.

---

## No Premature Autonomy

Do not introduce autonomous routing, recursive execution, persistent learning or adaptive tool selection until the required state, metrics, decision rules, failure handling, tests and safety boundaries have been explicitly defined.

---

# Development Progression

```text
Observe → Reason → Plan → Execute → Reflect → Remember → Improve
```

Phase 1 establishes the execution foundation.

Phase 2 adds stateful reflection and tool-selection capabilities without replacing the existing architecture.

---

# Next Step

Wire `AgentStateStore`, `ReflectionHistory` and `ToolMetrics` into `AGIOrchestrator`, following the Phase 2 plan. This changes the run loop, so it needs explicit approval before implementation.
