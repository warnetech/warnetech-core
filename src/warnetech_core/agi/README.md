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
- Canonical events: `goal_received`, `plan_generated`, `tool_selected`, `execution_completed`, `reflection_completed`.
- Phase 1 keeps no state between runs. Reflections and goals live in memory.

## Phase 2: in progress

Plan: `docs/plans/2026-11-09-phase-2/plan.md`.

Built, not yet called from `AGIOrchestrator`:

- `state.py`: `AgentStateStore` (goal history) and `ReflectionHistory`. JSON files, atomic writes.
- `tool_metrics.py`: `ToolMetrics`. Beta(1, 1) posterior ranking and Thompson-sampling selection. An untried tool scores 0.50.

Wiring these into the run loop changes orchestrator behaviour, so it needs approval first.

## Repository state

Git-ignored session files: `.clawness/sessions.json`, `.clawness/guard_sessions.json`, `.clawness/handoff.md`, `.clawness/handoff_autostart.json`.

Tracked project records: `.clawness/memory.md`, `.clawness/changelog.json`, `.clawness/version.json`, `.clawness/sast.json`.
