---
status: draft
---

# AGI Phase 2 plan

Source: `docs/agi-roadmap.md` (Phase 2 headings). Phase 1 closed at `c792a55`.

**Gate decisions (approved):**

- `fc2dfa3` landed before the plan was agreed. The modules stayed inert until P2-7, so they were kept, not reverted. The deviation is recorded here.
- P2-7 is opt-in: `AGIOrchestrator(..., enable_phase2=True, state_dir=path)`. Default is off. `state_dir` without the flag, or the flag without `state_dir`, raises `ValueError`.
- Persistence failures emit `persistence_failed` with payload `{component, error_type}` only. Exception messages are not written, since they may contain paths or request content. `execution_completed` is not reused.
- P2-7 and the AC5/AC6 tests are the Phase 2 integration gate.

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
| P2-4 | Persistent State | Reflection persistence and restart recovery | built, tested (`tests/test_agi_persistence_versioning.py`) |
| P2-5 | Tool Selection | Tool metrics store | built (`ToolMetrics`) |
| P2-6 | Tool Selection | Beta(1, 1) ranking and Thompson selection | built (`ToolMetrics`) |
| P2-7 | Integration | Opt-in wiring into `AGIOrchestrator` behind a constructor flag | built, tested (`tests/test_agi_phase2_wiring.py`) |

## Component boundaries

- `agi/state.py` owns durable records (goals, reflections). It does not decide anything.
- `agi/tool_metrics.py` owns outcome counts. It ranks and chooses, but does not execute.
- `telemetry.py` stays an event log. It is not a state store.
- `reflection.py` is extended, not replaced. P2-2 adds analysis helpers beside `Reflector`.
- `tools.py` remains the only executor.

## P2-2 outcome contract (APPROVED)

Implemented in `agi/outcome.py`, tests in `tests/test_agi_outcome.py`.

**Approved decisions:** confidence = `overall_quality`; no 0.70 threshold; invariant `success -> not retry`; invalid confidence raises `ValueError`.

**Deviation from `_coerce.py`:** `unit_interval` clamps and returns a default. The approved contract forbids clamping, so P2-2 uses its own strict check. The 'follow `_coerce.py`' wording in the approval was taken to mean the error style, not the clamping.

**Reason codes (approved):** `success`, `retry_recommended`, `failed`.

```python
@dataclass(frozen=True, slots=True)
class OutcomeAnalysis:
    success: bool
    retry: bool        # recommendation only; nothing executes a retry
    confidence: float  # 0.0 to 1.0
    reason: str        # bounded code, see below
```

Mapping from `ReflectionResult` (real fields only: `critiques`, `overall_quality`, `should_retry`, `has_errors`):

- `success = not reflection.has_errors`
- `retry = reflection.should_retry and not success`
- `reason` codes: `success`, `critical_critique`, `retry_requested`, `no_retry_requested`

Open questions for approval:

1. Confidence source. Proposed: `reflection.overall_quality`. The reflection has no separate confidence field.
2. Low-confidence threshold 0.70 (`confidence < 0.70`). Not used by the mapping above. Keep it out of P2-2, or add it as a rule?
3. Invariant `success == True` implies `retry == False`. Confirm.
4. Invalid confidence (NaN, out of range): reject with `ValueError`, following `_coerce.py`.

Acceptance (proposed): P2-2-AC1 success gives `retry=False`. AC2 has_errors with `should_retry` gives `retry=True`. AC3 has_errors without `should_retry` gives `retry=False`. AC4 confidence bounded. AC5 boundary tests at exactly the threshold if Q2 is kept. AC6 deterministic. AC7 no retry executed. AC8 tool selection untouched. AC9 no direct persistence.

## Data models

- `GoalRecord`: `goal_id`, `description`, `status`, `timestamp` (UTC ISO 8601).
- `ReflectionRecord`: `goal_id`, `overall_quality` in [0, 1], `should_retry`, `critique_count`, `timestamp`.
- Tool metrics file: `{tool_name: {successes, failures, total_latency_ms}}`.
- Files: `<state_dir>/goals.json`, `<state_dir>/reflections.json`, `<state_dir>/tool_metrics.json`.
- Every file is `{"schema_version": 1, <key>: <payload>}`. Keys: `records` for goals and reflections, `tools` for metrics. A file without `schema_version`, or with another version, raises `StateError`. No unversioned data is migrated, because none was ever written in production.

## Acceptance criteria

- AC1: Writes are atomic. A crash mid-write leaves the previous file intact. (Test exists: no temp files left behind.)
- AC2: Corrupt JSON raises `StateError`. It is never silently treated as empty. (Test exists.)
- AC3: Values outside their declared range are rejected. (Tests exist.)
- AC4: An untried tool scores 0.50. (Test exists.)
- AC5: With the flag off, result and telemetry match Phase 1, and no Phase 2 files are written. (Test: `test_ac5_...`.)
- AC6: With the flag on, one run writes a `running` and a final goal record, one reflection record, and tool outcomes. (Tests: `test_ac6_...`.)
- AC7: After a restart, the store reloads prior records and ranking reflects them. (Test: `test_ac7_...`.)

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
- Integration: scripted-gateway runs with the flag on and off (AC5, AC6), failure path, restart (AC7). All in `tests/test_agi_phase2_wiring.py`.

## Out of scope

- Beliefs, memories, and plans files from roadmap section 4.
- Composite ranking that combines latency or confidence (see the redundant-weighting note in `docs/agi-roadmap.md`).
- World model, multi-ghost coordination, evaluation harness (Phases 3 and 4).

## Verification

- `python -m pytest -q` (full suite)
- `ruff check src tests`
