---
status: audit complete, two policy decisions required, no runtime change
baseline: 5e805d4
---

# PB-5 runtime execution resilience audit

## Scope

Terminal behaviour after the selected tool exhausts the approved retry sequence: task outcome, ToolMetrics, reflection, outcome analysis, telemetry, persistence, restart, and failover. No runtime code was changed.

Method: traced `AGIOrchestrator.run()` terminal branch (`orchestrator.py` around lines 245-270), `_select_tool_name`, the `selections` map, `ToolRegistry.execute`, `_persist`, and state persistence. Searched for `analyze_outcome`, resume and plan-restore paths. Throwaway probe (not committed): a tool that always raises, reflection returning `should_retry=false` with no critiques.

## Probe result

```text
success=True
outcomes=['completed']
tool_errors=['RuntimeError: boom']
reflection_saw_error=False
```

## Findings

### PB5-F001 POLICY GAP: an unrecovered tool failure does not affect the terminal result

- Location: `orchestrator.py` terminal branch. `else` completes the task with `final_output = reflection.revised_output or output_text`.
- Current: a tool error is stored on `ToolResult.error`. If the reflection finds no errors, the task completes and the run reports `success=True`. This happens on the first attempt too, not only after retries.
- Expected: undecided. Either a tool failure should count against the task, or reflection alone should decide. The current code follows the second option, and this is not written down.
- Risk: a run reports success for work a tool did not do.
- Proposed action: decide the policy (see Decisions). Do not change the code before then.
- Red test: after the decision.
- Separate approval: YES.

### PB5-F002 POLICY GAP: reflection receives no tool error

- Location: `output_text` is built from the reasoning conclusion plus `last_tool.output`, only when `last_tool.success` is true. The reflection prompt has no tool-error field.
- Current: the reflection cannot see a failed tool call. Probe: `reflection_saw_error=False`.
- Expected: undecided, linked to PB5-F001.
- Separate approval: YES.

### PB5-F003 CORRECT: ToolMetrics accounting

- Location: `orchestrator.py` tool record (`self.tool_metrics.record(tool_call.tool_name, success=...)`) after each attempt. `save()` at run end writes the whole state.
- Each attempt is recorded once, for the tool that ran. The run-end save is persistence of state, not a second outcome record. No terminal duplicate exists.
- Evidence: `test_selection_records_outcome_only_for_executed_tool`, `test_metrics_attribute_retries_to_the_executed_tool`.

### PB5-F004 CORRECT: no failover after retry exhaustion

- Location: `_select_tool_name` is called only when `task.id not in selections`. The map is local to `run()`. After exhaustion the task leaves the retry loop and no further selection occurs for it.
- Evidence: `test_retry_keeps_metric_selected_tool_after_failure` runs an always-failing alternative through every attempt, including exhaustion, and asserts the hint never executes. That test already covers exhaustion.

### PB5-F005 CORRECT: terminal identity

- Location: `CycleRecord.tool_results[i].tool_name` is the executed tool. `task.mark_failed(...)` text is generic and names no tool.
- The executed tool is recoverable from the cycle record. The task-level failure message is intentionally not tool-specific.

### PB5-F006 OBSERVABILITY GAP: no terminal tool-failure event

- Location: `execution_completed` reports `success` from plan completion, `cycles`, and `confidence`. It has no tool identity and no tool-failure field.
- A tool failure that ends the run appears only in per-cycle `ToolResult` data and in ToolMetrics. Adding an event or field is an event-semantics decision. Decide it together with PB5-F001.

### PB5-F007 CORRECT: persistence failure isolation

- `_persist` catches the failure and emits `persistence_failed`. The terminal outcome is unchanged.
- Evidence: `test_persistence_failure_emits_bounded_event_and_run_continues`.

### PB5-F008 CORRECT: restart cannot resume a retry sequence

- Persisted state is goal records, reflection records and ToolMetrics only. Plans, tasks and retry counters are not persisted. A grep for resume or plan-restore paths found none.
- A restart starts a new run. Selection then runs for a new task, which is not a retry, so it is correct for that to select again.

### PB5-F009 NOT APPLICABLE: outcome analysis is not in the runtime path

- `analyze_outcome` is not called by the orchestrator. The terminal branch ignores `should_retry` once the retry budget is spent, which is correct. Retry eligibility is unchanged.
- Note: a retry recommendation after budget exhaustion is currently ignored. That is the intended behaviour and is not a new attempt.

### PB5-F010 DOCUMENTATION ONLY: tool-error behaviour was undocumented

- `docs/AGI.md` said errors "do not stop the run" but did not say that `success` ignores them. Corrected in this slice with a note pointing to PB5-F001.

### PB5-F011 COVERAGE GAP: terminal tool failure with a clean reflection

- No test covers "tool raises, reflection clean, run succeeds". Adding one now would lock in the behaviour that PB5-F001 may change. Add it with the decision.

### PB5-F012 CORRECT: selection controls are unchanged

- Default-off, tie, unknown-hint, and single-candidate behaviour are covered by the PB-1, PB-4 and metric-selection suites, which stay green.

## Decisions required

1. **PB5-F001 and F002.** When a tool fails and the reflection finds no errors:
   - A. The task completes and the run succeeds, as now. Record it in the spec as intended.
   - B. The task or run is not successful, because a tool failed. Reflection should receive the tool error too. This also changes outcome semantics, so it needs its own specification.
2. **PB5-F006.** If B is chosen, decide whether a terminal tool-failure event or field is needed, and its schema.

Until these are decided, no runtime change is made.

## Release-note summary

Baseline `5e805d4`.

Implemented:
- Candidate substitution is default-deny. Candidates come only from explicit `selection_group` membership. Tags grant no authority.
- Metric-informed selection exists and is off by default. It ranks only approved candidates.
- `tool_selected` reports the hint, candidates, selected tool and reason when metric selection is on.
- Selection happens once per task retry sequence. Retries reuse the originally selected tool. Automatic failover is not implemented.
- Production selection groups: none. Production substitution: none.

Known limitations (not changed):
- A tool failure that the reflection does not flag still lets the task and run succeed (PB5-F001). Reflection does not receive the tool error (PB5-F002).
- Terminal tool failures have no dedicated telemetry (PB5-F006).

Validation: 1052 passed, 2 skipped, lint clean.

## Closure

PB-5 is closed as an audit slice. Findings: 0 defects, 2 policy gaps (F001, F002), 1 observability gap (F006), 1 coverage gap (F011), 1 documentation correction (F010). No runtime fix was made. The next step needs the decisions above.
