---
status: implemented
baseline: 9e5ce7a
---

# PB-1: selection decision observability

## Context

Baseline `9e5ce7a`: Phase 1, Phase 2, the candidate layer and metric-informed selection are closed. Metric selection is off by default. No production `selection_group` is assigned, so no production tool substitution occurs.

Today a `tool_selected` event carries `cycle`, `task_id`, `tool_name`, and `selection_mode` (only when metric selection is enabled). It does not record the hinted tool or the candidate set, so a metric-driven substitution cannot be explained from telemetry alone.

Relevant code:

- `src/warnetech_core/agi/orchestrator.py`: `_select_tool_name`, the `tool_selected` emission, `_persist`.
- `src/warnetech_core/agi/candidates.py`: `resolve_candidates`, `pick_candidate`.
- `src/warnetech_core/agi/telemetry.py`: `TelemetryEvent`, the JSONL sink.
- Tests: `tests/test_agi_metric_selection.py`, `tests/test_agi_candidates.py`.

## Decisions (recommended, pending approval)

- **D1. No new record type.** Extend the existing `tool_selected` payload. A separate `SelectionDecision` object would duplicate the event.
- **D2. Default payload unchanged.** Add fields only when `enable_metric_selection=True`. Existing consumers see the same payload as before.
- **D3. Added fields when enabled:** `hinted_tool` (the planner's `tool_hint`, or `null`) and `candidates` (the names from `resolve_candidates`, in order). `tool_name` remains the winner. `selection_mode` already exists.
- **D4. No persistence, no new event name, no schema change.** `schema_version` stays at 1.
- **D5. Read-only.** The observability code must not call `pick_candidate` a second time, mutate metrics, or change candidate membership.

## Exact implementation requirements (after approval)

1. In `_select_tool_name`, return the candidate names along with the selection, without changing the choice. Either extend the return tuple or add a small internal dataclass. Keep the function's decision logic identical.
2. At the `tool_selected` emission, when `self._metric_selection` is true, add `hinted_tool` and `candidates` to the payload.
3. When `_metric_selection` is false, emit exactly the current payload.
4. Do not add public exports to `warnetech_core.agi`.
5. Update `src/warnetech_core/agi/README.md` to list the new fields.

## Tests first

Add to `tests/test_agi_metric_selection.py`, or a new `tests/test_agi_selection_observability.py`:

- `test_default_tool_selected_payload_unchanged`: flag off, the payload keys are exactly `cycle`, `task_id`, `tool_name`.
- `test_metric_payload_reports_hint_and_candidates`: flag on, two candidates, `hinted_tool == "hint"`, `candidates == ["hint", "alt"]`, `tool_name == "alt"`.
- `test_candidates_match_resolver_output`: the payload `candidates` equals `resolve_candidates(...)` names, for a three-tool registry with one outsider.
- `test_payload_tool_name_equals_executed_tool`: `tool_name` equals the tool recorded in `cycle.tool_results`.
- `test_no_hint_no_selection_event`: unknown hint emits no `tool_selected`, as before.
- `test_observability_does_not_mutate_metrics`: metrics file bytes unchanged by a selection-only read.

Expected red before implementation: the two metric-payload tests fail, because the fields do not exist yet.

## Acceptance criteria

- PB1-AC1: flag off, payload identical to baseline.
- PB1-AC2: flag on, `candidates` equals the resolver output in order.
- PB1-AC3: `tool_name` equals the executed tool.
- PB1-AC4: `selection_mode` is `metrics` or `default` and matches the path taken.
- PB1-AC5: observability cannot add a candidate.
- PB1-AC6: observability does not change metrics.
- PB1-AC7: no new persisted state, no `schema_version` change.
- PB1-AC8: unknown hint behaves as before.
- PB1-AC9: all `test_agi_candidates.py` tests stay green.
- PB1-AC10: all `test_agi_metric_selection.py` tests stay green.

## Sabotage and regression checks

Run each, confirm red, restore, confirm green. Do not commit the sabotage.

- S1: emit `hinted_tool` as `tool_name`. Expect payload tests red.
- S2: build `candidates` from `self.tools.list_tools()`. Expect candidate-match test red.
- S3: emit `tool_selected` before `_select_tool_name` runs. Expect ordering test red, or the executed-tool test red.
- S4: call `pick_candidate` a second time for the payload. Expect `test_observability_does_not_mutate_metrics` red or a winner mismatch.
- S5: always add the fields, even with the flag off. Expect `test_default_tool_selected_payload_unchanged` red.

## Commands

```bash
cd /home/user/warnetech-core
python -m pytest -q tests/test_agi_metric_selection.py tests/test_agi_candidates.py
python -m pytest -q                 # baseline: 1039 passed, 2 skipped
ruff check src tests                # baseline: clean
```

## Commit guidance

One commit for code and tests, one for docs if separate:

```text
feat(agi): add hinted tool and candidates to tool_selected when metric selection is on
docs(agi): record PB-1 selection observability
```

Do not commit sabotage edits. Before committing, `git diff` must show only the intended files.

## Stop conditions

Stop and report, without committing, if:

- the default `tool_selected` payload changes;
- any candidate or ranking decision changes;
- a persisted file or `schema_version` changes;
- the suite drops below 1039 passed, or any new test fails;
- a sabotage check leaves tests green;
- the implementation needs a second call to `pick_candidate` or `resolve_candidates`.

## Next step

Approve or amend D1 through D5. Once approved, start with the red tests above.


## Implementation record

- Event: `tool_selected` only. No new event, no `SelectionDecision` type.
- Metric-enabled payload adds `hinted_tool`, `candidate_tools` (resolver order, names only) and `selection_reason`, next to the existing `selection_mode`.
- Default-off payload is unchanged: `cycle`, `task_id`, `tool_name`.
- Reason codes, each tied to a real branch: `hint_default`, `single_candidate`, `metric_ranked`.
- Limitation: `metric_ranked` covers both a metric win and a tie that keeps the hint. Telling them apart would mean recomputing ranking, which PB-1 forbids. A tie is still visible through `candidate_tools` and `tool_name`.
- Tests: `tests/test_agi_selection_observability.py` (8 tests).
- Sabotage checks O1 to O5 all turned tests red. The file was restored byte-identical.
- Validation: 1047 passed, 2 skipped, lint clean.
