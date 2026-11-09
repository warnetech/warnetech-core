---
status: audit complete, one decision required
baseline: a010d15
---

# PB-3 execution identity audit

## Scope and method

Searched `src/warnetech_core/agi/` for `tool_hint`, `tool_name`, `selected_name`, `hinted_tool`, `record(`, `ToolResult`, `ToolCall`, and tool-related logs. Read the orchestrator selection, execution, metrics, retry and reasoning-context paths. Searched other packages for tool-identity use: none found outside AGI.

Probe (throwaway, not committed): a hint that always fails, a higher-ranked approved alternative, metric selection on, reflection requesting retry. Result: `executed=['hint','alt','alt']`, `result_tools=['hint','alt','alt']`, `outcomes=['retry','retry','completed']`.

No production code was changed.

## Findings

### PB3-F001 CORRECT: selection event
- Location: `orchestrator.py` `tool_selected` emission, around line 200.
- Identity: `tool_name` = selected tool. `hinted_tool` is added only when metric selection is on.
- Expected: selected/executed identity in `tool_name`, planner intent separately.
- Evidence: PB-1 tests `test_metric_winner_payload_reports_hint_candidates_and_winner` and `test_tie_reports_hint_winner_with_full_candidate_list`.

### PB3-F002 CORRECT: ToolMetrics attribution
- Location: `orchestrator.py` around line 222, `self.tool_metrics.record(tool_call.tool_name, ...)`.
- Identity: the tool that executed. `tool_call` is built from the selected name.
- Evidence: `test_selection_records_outcome_only_for_executed_tool`, and the probe above (each attempt recorded against the tool that ran).

### PB3-F003 CORRECT: execution results and errors
- Location: `tools.py` around lines 116-151, `ToolRegistry.execute`.
- Identity: `ToolResult.tool_name` and error logs use `call.tool_name`, the executed tool.
- No hint reference on this path.

### PB3-F004 CORRECT: candidate seed
- Location: `candidates.py` `resolve_candidates(tool_hint, ...)`.
- The hint is used only as the starting point for candidate discovery. It is planner intent, which is its correct meaning here.

### PB3-F005 CORRECT: planner field
- Location: `planning.py` `TaskNode.tool_hint` and the planner prompt.
- Advisory planner intent, as documented.

### PB3-F006 CORRECT: reasoning context
- Location: `orchestrator.py` around lines 229-230, `last_tool.output`.
- Uses the executed tool's output. Contains no hint identity.

### PB3-F007 CORRECT: no identity in persisted or reflection records
- `state.py` `GoalRecord`, `ReflectionRecord` and `tool_metrics.json` carry no tool identity beyond ToolMetrics keys, which are executed names.
- Reflection receives text only. `ReflectionResult` has no tool field.
- Outcome analysis has no tool field.

### PB3-F008 AMBIGUOUS, DECISION REQUIRED: retry reselects the tool
- Location: `orchestrator.py` retry branch (around lines 245-260). A failed attempt sets the task back to `PENDING`, and the next cycle calls `_select_tool_name` again.
- Effect: the failed attempt's outcome lowers that tool's ranking before the retry. With metric selection on, the retry can execute a different approved candidate. Probe: `hint` failed, then `alt` ran on retry.
- Identity attribution is correct. Each attempt is recorded for the tool that ran.
- The behaviour is not in any approved specification. The spec excludes automatic failover unless a separate specification defines failure eligibility, retry limits and side-effect safety. This is failover in effect.
- Risk if left as is: a failing tool is replaced by a different tool on retry, without review of side effects or idempotency for the new tool.
- Options for the owner:
  1. Retries keep the failed attempt's tool. Selection runs once per task. Requires a change, so a separate approved fix.
  2. Retries may reselect. Accept it and write a failover specification covering eligibility, limits, idempotency and telemetry.
- PB-3 does not decide this. Changing it would be a behaviour change and an authority change, which PB-3 excludes.

### PB3-F009 DOCUMENTATION ONLY: docs coverage
- The AGI README and PB-1 brief use `hinted`, `selected` and `executed` consistently.
- Not fully checked: `docs/AGI.md` and the older roadmap sections. These were not inspected in this audit and are not classified.

## Test coverage gap

No existing test covers retry after a failed attempt under metric selection. Adding one would characterise the current behaviour. It is not a defect fix, so it waits for decision PB3-F008.

## Classification summary

- CORRECT: 7 (F001-F007)
- DEFECT: 0
- AMBIGUOUS, decision required: 1 (F008)
- DOCUMENTATION ONLY: 1 (F009), partial

## Stop condition reached

PB3-F008 needs a decision on failover semantics. Per the PB-3 stop conditions, it is reported and not implemented.

## Open decision

Choose option 1 or option 2 for PB3-F008, then PB-3 can continue with the approved fix.
