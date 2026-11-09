---
status: closed
baseline: 2877bf2
---

# PB-4 retry selection stability

## Decision

An ordinary retry keeps the tool selected for the original attempt. It does not re-run candidate selection. Automatic reselection and failover are not approved.

## Change

- `orchestrator.py`: `run()` keeps a per-run `selections` map keyed by task id. Selection runs on the first attempt only. Later attempts reuse the stored decision.
- `tool_selected` is still emitted for each attempt, with the stored decision. This keeps the existing per-attempt telemetry contract. The event is not new.
- Retry counts, retry eligibility, ToolMetrics attribution, ranking, candidates and persistence are unchanged.

## Tests

`tests/test_agi_retry_selection.py` (5 tests):
- F008 regression: metric-selected `alt` fails. The retry must run `alt`, not `hint`. Red before the fix (`['alt', 'hint', 'hint']`).
- Changed metrics between attempts do not switch the tool.
- Default-off retry repeats the hinted tool.
- Retry count is unchanged (1 initial attempt + 2 retries).
- Metrics attribute every attempt to the tool that ran.

## Sabotage checks (each restored, not committed)

- RS1 reselect every cycle: 3 failed
- RS2 retry runs the planner hint: 3 failed
- RS3 extra attempts (bounded stand-in for a counter reset, which could loop forever): 4 failed
- RS4 attribute executed outcome to hint: 3 failed
- RS5 reselect only on retry: 3 failed

## Validation

1052 passed, 2 skipped, lint clean.

## Not in this slice

Automatic failover to another candidate. It needs its own specification covering failure eligibility, attempt limits, idempotency and telemetry.
