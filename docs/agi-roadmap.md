# Warnetech-Core AGI Expansion Roadmap

## Status (Phase 1 closed, Phase 2 closed)

This document is the long-range vision. The status below records what shipped on
`ccr-2ddd16bf-wsxi86`. Sections after it are design notes; where they conflict with
the code, the code is authoritative.

### Phase 1 (closed)

| Task | Item | Where |
|---|---|---|
| T1 | Planner: duplicate task titles map to every matching task ID, so a dependency waits for all matches | `agi/planning.py` |
| T2 | `Goal.id`: stable identifier used to correlate telemetry | `agi/planning.py` |
| T3 | Capability metadata: `ToolDefinition` gained `version` and `dependencies`; `CapabilityRegistry` validates dependencies | `agi/tools.py`, `agi/capability_registry.py` |
| T4 | Confidence: deterministic 0.0-1.0 run-level score; supporting evidence raises it, contradictions lower it | `agi/confidence.py` |
| T5 | Telemetry: opt-in JSONL sink with five events: `goal_received`, `plan_generated`, `tool_selected`, `execution_completed`, `reflection_completed` | `agi/telemetry.py` |
| T6 | Integration of the above into `AGIOrchestrator` | `agi/orchestrator.py` |
| T7 | Documentation and validation (this section) | `docs/agi-roadmap.md` |

Runtime behaviour that the docs must not overstate:

- The capability registry is stored on the orchestrator but the run loop does not read it.
  Tools are not selected from capability metadata.
- Confidence is computed once, when a run finishes. It is not a gate between tool
  execution and reflection.
- Telemetry is exported from `warnetech_core.agi.telemetry`. It is not re-exported from
  the `agi` package.

**Phase 1 persistence boundary.** Reflection results and goal state live in memory only.
Phase 1 has no persistent reflection history, goal history, tool-performance history or
state store.

### Phase 2 (closed)

**Status: CLOSED.** No approved Phase 2 production work remains.

Completed:

- Outcome analysis (`agi/outcome.py`, P2-2): `success`, `retry` recommendation, `confidence`, `reason`. Analysis only. Confidence comes from `overall_quality` and is validated strictly.
- Schema-versioned persistence (P2-4): goals, reflections and tool metrics are written as `schema_version: 1`. Missing or unknown versions raise `StateError`. Reflection and goal restart recovery is tested.
- Tool outcome metrics (P2-6 infrastructure): `ToolMetrics` records outcomes and ranks tools with a Beta(1, 1) prior.
- Opt-in integration (P2-7): `AGIOrchestrator(enable_phase2=True, state_dir=...)`. Off by default. Persistence failures emit `persistence_failed` and do not fail the run.

Deliberately not done:

- Metric-informed tool selection is opt-in (`enable_metric_selection=True`, off by default). It ranks only explicit `selection_group` candidates. Production selection groups are none, so no production tool is substituted.
- Retries reuse the tool selected for the task. They do not re-run selection, so they are not failover (PB-4). Automatic failover is not implemented.
- `OutcomeAnalysis` is not persisted. It is derived from the persisted reflection.

Plan: `docs/plans/2026-11-09-phase-2/plan.md`.

### Repository hygiene

Runtime session files are git-ignored: `.clawness/sessions.json`,
`.clawness/guard_sessions.json`, `.clawness/handoff.md`, `.clawness/handoff_autostart.json`.
Durable records stay tracked: `.clawness/memory.md`, `.clawness/changelog.json`,
`.clawness/version.json`, `.clawness/sast.json`.

## Current AGI Baseline

Baseline: `162762a`

Status:
- Phase 1: closed
- Phase 2: closed
- Candidate layer: closed
- Metric-informed tool selection: closed
- PB-1 selection observability: closed
- PB-2 production selection-group governance: closed
- PB-3 execution identity audit: closed
- PB-4 retry selection stability: closed
- PB-5 runtime resilience audit: closed
- PB-6 terminal tool failure semantics: closed (spec `8285db0`)

Validation: 1062 passed, 2 skipped. `ruff check src tests` clean. Four pre-existing Ruff findings remain outside src/tests (examples/, scripts/, vendor/).

Runtime policy:
- Metric-informed selection remains off by default.
- No production selection groups are assigned, so production tool substitution is inactive.
- Tool selection occurs once per ordinary task retry sequence.
- Retries reuse the originally selected tool.
- Retry-time reselection is not permitted.
- Automatic tool failover is not implemented.

Governance:
- Any future production `selection_group` assignment requires review through the PB-2 framework (`docs/plans/2026-11-09-pb2-selection-group-review/framework.md`).
- Any behaviour that automatically changes the executing tool after an execution failure is automatic failover. It requires a separate approved specification. It is not unfinished PB-4 work.

## Post-PB-6 Controlled Autonomy Roadmap

Do not reopen closed contracts without a new specification: selection_group semantics, resolve_candidates(), ToolMetrics ranking, cold-start and tie behaviour, the metric-selection default, retry eligibility and limits, same-tool retry, terminal-failure semantics, the persistent schema, OutcomeAnalysis, and reflection failure confidentiality.

Each step that increases runtime authority follows this path: specification, acceptance criteria, tests, implementation, sabotage checks, independent review, closure baseline.

Target progression: observe, plan, select, execute, verify, reflect, remember, adapt, evaluate. Authority increases gradually. Unrestricted autonomy is out of scope.

### PB-7 Execution Verification (next, PROPOSED)

Separate tool execution from task satisfaction: tool result, then verification, then reflection, then outcome. Verification answers whether the task was satisfied, not whether a tool returned. Spec: `docs/plans/2026-11-09-pb7-execution-verification/spec.md`. It does not retry, fail over, select tools, change ToolMetrics, or alter the schema.

### PB-8 AGI Evaluation Harness

Measurable, interpretable benchmarks, grouped PLAN-, SELECT-, EXEC-, VERIFY-, REFLECT-, OUTCOME-, STATE-, RECOVERY- and SAFETY-. No single "AGI score". Results live in test or evaluation artefacts, never in runtime agent memory.

### PB-9 Goal and Plan Lifecycle

Explicit goal states: CREATED, PLANNED, EXECUTING, VERIFYING, REFLECTING, COMPLETED, FAILED, BLOCKED. Keep task, goal, persistence and verification failure distinct. Plans are versioned (plan_id, plan_version, goal_id, supersedes_plan). An active plan is never mutated silently.

### PB-10 Durable Episodic Experience

Remember bounded execution experience across runs: goal class, executed tool, verification result, outcome, timestamp, context identifiers. Never store chain-of-thought, hidden reasoning, credentials, raw exception messages or full prompt histories. Keep FACT, EXPERIENCE, METRIC and REFLECTION as separate stores.

### PB-11 Evidence and Belief Model

Distinguish observed evidence, derived conclusions and uncertain hypotheses. Every durable belief traces to evidence references: external source, execution evidence, verified state, or another recorded source. A model's statement is not a fact.

### PB-12 Production Tool Substitution Pilot

After PB-7 and PB-8. At most one narrowly reviewed production selection group, using the PB-2 framework. Tools must be genuinely interchangeable. The workspace_read/run_code class of error must stay impossible.

### PB-13 Adaptive Selection Policy

Only after real production metrics and the evaluation harness exist. Each new ranking signal defines meaning, range, source, cold-start and missing-data behaviour, weight, tie effect, persistence and tests. Avoid redundant signals.

### PB-14 Explicit Tool Failover Policy

Automatic failover stays NOT IMPLEMENTED until this slice specifies: eligible and ineligible failures, candidate exclusion, attempt limits, retry ordering, side-effect handling, partial success, ToolMetrics, telemetry, verification and restart behaviour. Classify operations as READ_ONLY, IDEMPOTENT_MUTATION, NON_IDEMPOTENT_MUTATION or UNKNOWN. UNKNOWN means no automatic failover.

### PB-15 Long-Horizon Goal Execution

Goals survive several plan, execute, verify and reflect cycles, each bounded. Budgets cover cycles, tool executions, retries, plan revisions, elapsed time, model calls and cost. Exhausting a budget yields BLOCKED or INCOMPLETE, never unbounded execution.

### PB-16 Multi-Agent / Multi-Ghost Coordination

Late. Needs verification, evaluation, goal lifecycle, evidence model, experience model, bounded autonomy and conflict semantics first. Consensus is not majority vote. Consider provenance, source independence, verification status, confidence and contradictions. Every claim keeps its provenance.

### PB-17 Autonomy Safety and Release Gate

Required before any broad autonomous release. Reviews candidate authority, tool privilege, side effects, retry, failover, persistence, data exposure, telemetry, goal loops, budget enforcement, rollback and evaluation results. No feature bypasses this gate because its tests pass.

### Security principles

- Historical success is not execution authority.
- An LLM recommendation is not execution authority.
- A shared tag is not substitution authority.
- Authority comes from explicit contracts, approved candidate boundaries, tool permissions and verified policy.

### Tool privilege model (future, not implemented)

Conceptual categories: READ, WRITE, EXECUTE, NETWORK, ADMIN, EXTERNAL_MUTATION. Selection and failover must never silently escalate privilege.

### Telemetry direction (future, each event needs its own schema review)

goal_received, plan_generated, tool_selected, execution_completed, verification_completed, reflection_completed, outcome_completed, goal_completed, goal_failed.

### State migration policy

Persistent state stays versioned. No silent schema semantic change. A future incompatible change needs a new specification, migration tests, then the new version. Missing or unknown versions are never treated as current.

### Release governance

Each behaviour-changing slice: PROPOSED, APPROVED, TESTS-FIRST, IMPLEMENTED, INDEPENDENT REVIEW, CLOSED. Record baseline, implementation and closure commits, tests, skips, lint, known limitations and deferred behaviour. Authority-sensitive slices get a read-only GitHub Copilot CLI review. Copilot does not modify files.

### Repository quality track

Create MAINT-1 (Repository-wide Ruff cleanup) only if the four findings matter. Do not mix it into AGI behaviour slices.

### Order of work

PB-7, PB-8, PB-9, PB-10, PB-11, PB-12, PB-13, PB-14, PB-15, PB-16, PB-17. PB-14, PB-15 and PB-16 do not start before PB-7 and PB-8 are closed.

## Executive Summary

The next stage of development for `warnetech/warnetech-core` should focus on transforming the AGI layer from a collection of intelligent components into a structured autonomous reasoning system. Rather than simply adding more agents, prompts, or AI wrappers, the platform should gain stronger capabilities in planning, memory integrity, self-reflection, world modelling, and multi-agent collaboration.

The following roadmap outlines the highest-value additions that would significantly increase AGI capability while remaining aligned with the existing Warnetech architecture.

## 1. AGI Capability Registry

### Purpose

Allow the AGI to dynamically understand what functions, tools, and services are available without hardcoded knowledge.

### Benefits

- Dynamic capability discovery
- Autonomous planning
- Tool selection
- Self-description
- Future plugin ecosystem support
- Runtime feature management

### Example Structure

```json
{
  "capability": "memory_reconstruction",
  "version": "1.0.0",
  "description": "Rebuilds long-term memory structures",
  "inputs": [
    "ghost_id",
    "memory_range"
  ],
  "outputs": [
    "reconstruction_plan"
  ],
  "dependencies": [
    "retention_engine",
    "vector_store",
    "control_plane"
  ]
}
```

### Suggested Directory

```text
agi/
└── capabilities/
    ├── planner.json
    ├── memory.json
    ├── reflection.json
    └── reconstruction.json
```

## 2. Goal Decomposition Engine

### Purpose

Convert high-level objectives into executable task graphs.

Current AI systems often perform isolated actions. An AGI requires the ability to break large goals into smaller executable steps automatically.

### Example

User Goal:

```text
Reconstruct Ghost Alpha
```

Generated Plan:

```yaml
goal: reconstruct_ghost_alpha

tasks:
  - collect_memories
  - identify_timeline
  - detect_gaps
  - fill_gaps
  - validate_reconstruction
  - assign_confidence
  - generate_report
```

### Proposed Components

```python
class GoalPlanner:
    def decompose(goal):
        pass

class TaskGraph:
    pass

class DependencyResolver:
    pass
```

### Benefits

- Autonomous operation
- Reduced manual orchestration
- Better scalability
- Complex mission execution

## 3. Memory Confidence Framework

### Purpose

Every memory stored or reconstructed should be associated with a confidence score.

Without confidence tracking, reconstructed memories become difficult to trust.

### Example

```json
{
  "memory_id": "abc123",
  "confidence": 0.94,
  "sources": 7,
  "contradictions": 0,
  "time_validated": true
}
```

### Confidence Factors

**Positive signals**

- Multiple supporting sources
- Matching timelines
- Verified event records
- Historical agreement

**Negative signals**

- Contradictory memories
- Missing records
- Incomplete timelines
- Corrupt source data

### Benefits

- Hallucination prevention
- Memory integrity
- Trust scoring
- AGI self-validation

## 4. Persistent Agent State System

### Purpose

Allow AGI processes to survive restarts and continue previous work.

### Directory Structure

```text
agent_state/
├── goals.json
├── beliefs.json
├── plans.json
├── memories.json
├── capabilities.json
└── reflections.json
```

### Stored Information

**Goals**

```json
{
  "goal": "reconstruct_alpha",
  "status": "in_progress"
}
```

**Plans**

```json
{
  "plan_id": "plan_001",
  "completion": 73
}
```

**Beliefs**

```json
{
  "ghost_exists": true,
  "confidence": 0.91
}
```

### Benefits

- Long-running intelligence
- Restart recovery
- Continuous learning
- Agent evolution

## 5. Reflection and Self-Critique Engine

### Purpose

Enable the AGI to review its own actions and outcomes.

Most systems perform actions but do not evaluate their effectiveness.

### Workflow

```text
PLAN
 ↓
EXECUTE
 ↓
REFLECT
 ↓
LEARN
 ↓
IMPROVE
```

### Example

```python
result = executor.run(plan)

reflection = reflector.review(
    goal=goal,
    plan=plan,
    result=result
)
```

### Reflection Output

```json
{
  "success": true,
  "confidence": 0.89,
  "issues_detected": [],
  "recommended_improvements": [
    "increase source coverage"
  ]
}
```

### Benefits

- Continuous improvement
- Error detection
- Autonomous optimisation
- Reduced failure rates

## 6. World Model Knowledge Graph

### Purpose

Move from storing memories to understanding relationships.

A memory database alone is not AGI. A knowledge graph creates understanding.

### Example Relationships

```text
Ghost Alpha
    │
    controls
    │
Retention Service

Retention Service
    │
    depends_on
    │
Memory Database
```

### Example Model

```json
{
  "entity": "GhostAlpha",
  "relationship": "controls",
  "target": "RetentionService"
}
```

### Suggested Structure

```text
world_model/
├── entities/
├── relationships/
├── observations/
└── predictions/
```

### Benefits

- Reasoning
- Relationship discovery
- Predictive analysis
- Decision support

## 7. Multi-Ghost Coordination Framework

### Purpose

Allow multiple ghost entities to collaborate on problem solving and reconstruction.

This is one of the highest-value future features.

### Components

```python
class GhostCoordinator:
    pass

class ConsensusEngine:
    pass

class ConflictResolver:
    pass

class TimelineMerger:
    pass
```

### Example

Ghost Alpha:

```json
{
  "fact": "Server restarted at 2:00"
}
```

Ghost Beta:

```json
{
  "fact": "Server restarted at 1:59"
}
```

Consensus output:

```json
{
  "fact": "Server restarted approximately 2:00",
  "supported_by": [
    "ghost_alpha",
    "ghost_beta",
    "system_logs"
  ],
  "confidence": 0.95
}
```

### Features

- Timeline merging
- Consensus generation
- Conflict resolution
- Shared reasoning
- Source attribution

## 8. AGI Evaluation Harness

### Purpose

Measure AGI improvement over time.

If performance cannot be measured, it cannot be improved.

### Suggested Command

```shell
warnetech agi-eval run
```

### Evaluation Categories

- **Reasoning:** Can the system solve complex tasks?
- **Planning:** Can the system break goals into actions?
- **Memory:** Can it recall and reconstruct information?
- **Reflection:** Does it identify mistakes?
- **Tool usage:** Can it choose appropriate tools?

### Example Result

```json
{
  "date": "2026-11-09",
  "reasoning_score": 88,
  "planning_score": 93,
  "memory_score": 91,
  "reflection_score": 84,
  "overall_score": 89
}
```

## 9. Autonomous Tool Selection System

### Purpose

Allow the AGI to choose tools dynamically instead of relying on predefined tool chains.

### Current Model

```python
handler.run()
```

### Future Model

```python
best_tool = planner.choose_tool(task)

best_tool.execute()
```

### Example Decision

```yaml
task: reconstruct_memory

candidates:
  - vector_search
  - graph_search
  - replay_engine

winner: graph_search
```

### Benefits

- Adaptability
- Better efficiency
- Reduced manual configuration
- Increased autonomy

## 10. AGI Telemetry Layer

### Purpose

Record every major reasoning and execution event.

### Example Event

```json
{
  "thought_id": "thought_1001",
  "goal": "reconstruct_alpha",
  "selected_plan": "plan_78",
  "confidence": 0.87,
  "execution_time_ms": 223,
  "status": "completed"
}
```

### Suggested Structure

```text
telemetry/
├── thoughts/
├── plans/
├── actions/
├── reflections/
└── metrics/
```

### Integration

Connect telemetry into:

- Security events
- Threat events
- Retention logs
- Audit trails
- Behaviour analysis

## Recommended Priority Order

**Phase 1 (Immediate)**

1. Goal Decomposition Engine
2. Memory Confidence Framework
3. Capability Registry
4. AGI Telemetry

**Phase 2 (Core AGI)**

5. Reflection Engine
6. Persistent Agent State
7. Autonomous Tool Selection

**Phase 3 (Advanced Intelligence)**

8. World Model Knowledge Graph
9. Multi-Ghost Coordination
10. Consensus & Conflict Resolution

**Phase 4 (Validation)**

11. AGI Evaluation Harness
12. Benchmark Testing Framework
13. Long-Term Learning Metrics

## Final Architecture Vision

```text
 +-------------------+
 |     USER GOAL     |
 +---------+---------+
           |
           v
 +-------------------+
 |   Goal Planner    |
 +---------+---------+
           |
           v
 +-------------------+
 | Capability Registry|
 +---------+---------+
           |
           v
 +-------------------+
 |   Tool Selector   |
 +---------+---------+
           |
           v
 +-------------------+
 | Execution Engine  |
 +---------+---------+
           |
           v
 +-------------------+
 | Reflection Engine |
 +---------+---------+
           |
           v
 +-------------------+
 |    World Model    |
 +---------+---------+
           |
           v
 +-------------------+
 | Multi-Ghost Layer |
 +---------+---------+
           |
           v
 +-------------------+
 |  AGI Telemetry    |
 +-------------------+
```

**Conclusion:** The biggest opportunity for `warnetech-core` is not adding more AI models, but building the planning, memory, reflection, knowledge graph, and multi-ghost coordination infrastructure that allows intelligence to persist, reason, validate itself, and improve over time. This foundation would move the project much closer to a true autonomous reasoning platform rather than a collection of independent AI components.

---

## Implementation notes (added on adoption)

- Phase 1 code lives in the existing package `src/warnetech_core/agi/`, not in a new root-level `agi/` directory. `CLAUDE.md` places AGI modules there.
- Example goals in code and docs must be neutral (for example "Analyse operational telemetry") until the domain model for ghost reconstruction is settled. The Ghost Alpha examples above are illustrative only and are not a commitment to build that feature.
- All model calls go through `AIGateway.complete()`, per `CLAUDE.md` rule 7.
