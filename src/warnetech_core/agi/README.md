# AGI package

Planning, tool execution, reflection, confidence scoring, capability metadata and telemetry for the AGI orchestrator.

This is the only AGI package. Extend it rather than adding a parallel `agi/` package.

## Phase 1: closed

| Task | Item | Module |
|---|---|---|
| T1 | Duplicate task titles: a dependency waits for every match | `planning.py` |
| T2 | `Goal.id` as the correlation key | `planning.py` |
| T3 | Capability metadata: `ToolDefinition.version` and `.dependencies` | `tools.py`, `capability_registry.py` |
| T4 | Run-level confidence, 0.0 to 1.0 | `confidence.py` |
| T5 | JSONL telemetry | `telemetry.py` |
| T6 | Integration into `AGIOrchestrator` | `orchestrator.py` |
| T7 | Documentation and validation | this file, `docs/agi-roadmap.md` |

Runtime limits:

- The capability registry is stored but the run loop does not read it. Tools are not selected from metadata.
- Confidence is computed once, when a run finishes. It does not gate execution or reflection.
- Telemetry is not re-exported from the package. Import it as `from warnetech_core.agi.telemetry import Telemetry, TelemetryEvent`.
- Canonical events: `goal_received`, `plan_generated`, `tool_selected`, `execution_completed`, `reflection_completed`, and `persistence_failed` (added in Phase 2).
- Phase 1 kept no state between runs. Phase 2 persistence is off unless `enable_phase2=True` and `state_dir` are both passed.

## Phase 2: in progress

Plan: `docs/plans/2026-11-09-phase-2/plan.md`.

Wired into `AGIOrchestrator` behind `enable_phase2=True, state_dir=...` (off by default):

- `state.py`: `AgentStateStore` (goal history) and `ReflectionHistory`. JSON files, atomic writes.
- `tool_metrics.py`: `ToolMetrics`. Beta(1, 1) posterior ranking and Thompson-sampling selection. An untried tool scores 0.50.

Ranking and selection are recorded but not yet used to choose tools. That needs its own approval.

## Repository state

Git-ignored session files: `.clawness/sessions.json`, `.clawness/guard_sessions.json`, `.clawness/handoff.md`, `.clawness/handoff_autostart.json`.

Tracked project records: `.clawness/memory.md`, `.clawness/changelog.json`, `.clawness/version.json`, `.clawness/sast.json`.
