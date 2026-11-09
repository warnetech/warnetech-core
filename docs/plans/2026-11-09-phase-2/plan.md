---
status: draft
---

# AGI Phase 2 plan

Source: `docs/agi-roadmap.md` (Phase 2 headings). Phase 1 closed at `c792a55`.

**Gate:** Phase 2 modules `state.py` and `tool_metrics.py` were committed (`fc2dfa3`) before this plan was agreed. They are not called from `AGIOrchestrator`. Wiring them in is blocked until this plan is approved.

## Objectives

1. Reflections and goal outcomes survive a process restart.
2. Tool choice uses measured outcomes, with a cold-start prior.
3. Existing Phase 1 behaviour is unchanged unless a flag is set.

## Task numbering

| ID | Area | Task | Status |
|---|---|---|---|
| P2-1 | Reflection Engine | Reflection history store | built (`ReflectionHistory`) |
| P2-2 | Reflection Engine | Outcome analysis: derive success and retry signal from `ReflectionResult` | not started |
| P2-3 | Persistent State | State store: goal history | built (`AgentStateStore`) |
| P2-4 | Persistent State | Reflection persistence and restart recovery | not started |
| P2-5 | Tool Selection | Tool metrics store | built (`ToolMetrics`) |
| P2-6 | Tool Selection | Beta(1, 1) ranking and Thompson selection | built (`ToolMetrics`) |
| P2-7 | Integration | Opt-in wiring into `AGIOrchestrator` behind a constructor flag | not started, needs approval |

## Component boundaries

- `agi/state.py` owns durable records (goals, reflections). It does not decide anything.
- `agi/tool_metrics.py` owns outcome counts. It ranks and chooses, but does not execute.
- `telemetry.py` stays an event log. It is not a state store.
- `reflection.py` is extended, not replaced. P2-2 adds analysis helpers beside `Reflector`.
- `tools.py` remains the only executor.

## Data models

- `GoalRecord`: `goal_id`, `description`, `status`, `timestamp` (UTC ISO 8601).
- `ReflectionRecord`: `goal_id`, `overall_quality` in [0, 1], `should_retry`, `critique_count`, `timestamp`.
- Tool metrics file: `{tool_name: {successes, failures, total_latency_ms}}`.
- Files: `<state_dir>/goals.json`, `<state_dir>/reflections.json`, `<state_dir>/tool_metrics.json`. No schema version field yet. P2-4 adds one.

## Acceptance criteria

- AC1: Writes are atomic. A crash mid-write leaves the previous file intact. (Test exists: no temp files left behind.)
- AC2: Corrupt JSON raises `StateError`. It is never silently treated as empty. (Test exists.)
- AC3: Values outside their declared range are rejected. (Tests exist.)
- AC4: An untried tool scores 0.50. (Test exists.)
- AC5: With the flag off, `AGIOrchestrator` output and telemetry are identical to Phase 1. (Not written.)
- AC6: With the flag on, one run writes one goal record and one reflection record. (Not written.)
- AC7: After a restart, the store reloads prior records and ranking reflects them. (Not written.)

## Failure behaviour

- State directory missing: created on first write.
- State directory unwritable: the write raises. The orchestrator must catch it, emit a telemetry event, and continue the run without persistence. (Not decided: which event name. Proposed: reuse `execution_completed` with an error field, not a new event name.)
- Corrupt state file: `StateError`. The operator must repair or move the file. No automatic reset.
- Concurrent writers: not supported. One process per state directory. Documented, not enforced.

## Migration

- None for Phase 1 users. Persistence is off by default.
- P2-4 introduces `schema_version: 1` before any production data is written.

## Tests

- Unit: `tests/test_agi_state.py`, `tests/test_agi_tool_metrics.py` (present).
- Integration: scripted-gateway run with the flag on and off, asserting AC5 and AC6 (to write in P2-7).
- Restart: write, drop the objects, reload, assert AC7 (to write in P2-4).

## Out of scope

- Beliefs, memories, and plans files from roadmap section 4.
- Composite ranking that combines latency or confidence (see the redundant-weighting note in `docs/agi-roadmap.md`).
- World model, multi-ghost coordination, evaluation harness (Phases 3 and 4).

## Verification

- `python -m pytest -q` (full suite)
- `ruff check src tests`
