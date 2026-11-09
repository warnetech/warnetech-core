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

## Phase 2: closed

Plan: `docs/plans/2026-11-09-phase-2/plan.md`. Closed: outcome analysis (P2-2), schema-versioned persistence and restart recovery (P2-4), opt-in integration and persistence-failure isolation (P2-7). `ToolMetrics` records and ranks tool outcomes (P2-6 infrastructure).

- `tool_selected` (PB-1): with metric selection on, also reports `hinted_tool`, `candidate_tools` and `selection_reason` (`hint_default`, `single_candidate`, `metric_ranked`). Telemetry only. It does not change the decision.
- Metric-informed tool selection is closed (`fffaf6b`). Production selection groups: none, so no production tool substitution occurs yet.
- Runtime tool selection uses `ToolMetrics` ranking only when `enable_metric_selection=True` (also requires `enable_phase2=True` and `state_dir`). Candidates come from explicit `ToolDefinition.selection_group`, never from tags. Off by default.
- `OutcomeAnalysis` is not persisted.
- Phase 2 persistence is off unless `enable_phase2=True` and `state_dir` are both passed.

No further Phase 2 production work is approved.

## Repository state

Git-ignored session files: `.clawness/sessions.json`, `.clawness/guard_sessions.json`, `.clawness/handoff.md`, `.clawness/handoff_autostart.json`.

Tracked project records: `.clawness/memory.md`, `.clawness/changelog.json`, `.clawness/version.json`, `.clawness/sast.json`.
