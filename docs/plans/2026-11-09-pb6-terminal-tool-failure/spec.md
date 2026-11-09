---
status: proposed, implementation blocked until open decisions are approved
baseline: 1b3ff16
source: PB-5 PB5-F001, PB5-F002, PB5-F006, PB5-F011
---

# PB-6 terminal tool failure semantics

## Decision (from PB-5)

Option B: a terminal tool execution failure must not produce a successful task or run merely because the reflection reports no errors. The failure must reach the reflection and outcome path, so the terminal state and the success flag cannot contradict each other.

This changes outcome semantics. It is implemented only after this spec is approved.

## Current behaviour (PB-5 probe)

- Tool raises. The error is stored on `ToolResult.error`.
- Reflection finds no errors and does not request a retry.
- Task completes. Run reports `success=True`.
- Reflection never receives the tool error.

## Proposed contract

1. **Attempt failure.** An attempt whose tool result has `success=False` is a failed attempt.
2. **Reflection input.** The reflection prompt includes a bounded line for a failed attempt: tool name and error class only, for example `Tool failed: lookup (RuntimeError)`. The exception message is not included, because it may contain paths, request data or credentials.
3. **Attempt outcome.** A failed attempt is not completed. The cycle outcome is `failed`, unless the retry rules below apply.
4. **Retry.** Retry eligibility is unchanged. A reflection that requests a retry still retries, and the existing budget applies. A tool failure alone does not add a retry (see D2).
5. **Terminal result.** The task result is decided by the final attempt. If the final attempt failed its tool, the task fails with a bounded reason that names the tool and error class. The run's `success` is false if any task fails.
6. **Earlier failures.** A failure followed by a successful final attempt does not fail the task (see D1).
7. **ToolMetrics.** Unchanged. Each attempt is still recorded once, against the tool that ran.
8. **Telemetry.** `execution_completed.success` follows the terminal result. No new event (see D3).
9. **Selection.** Unchanged. Retry still reuses the originally selected tool (PB-4).
10. **Persistence.** Unchanged. `schema_version: 1`.

## Open decisions (must be approved before implementation)

- **D1. Recovered failure.** Attempt 1 fails its tool; a retry succeeds. Proposed: the task succeeds, and the failure stays in cycle and metrics records. Alternative: the task is marked as having a failed attempt. This would need a new field.
- **D2. Retry on tool failure.** Proposed: no new retry. A tool failure with a clean reflection ends the task as failed. Alternative: a tool failure always requests a retry. That would expand retry eligibility, which PB-4 excludes.
- **D3. Telemetry.** Proposed: `execution_completed.success` carries the result. Optional field `tool_failed: bool` on `execution_completed`. Alternative: a new `task_failed` event. Proposed is the smaller change.
- **D4. Reflection wording.** Proposed format: `Tool failed: <name> (<ErrorClass>)`. Confirm the wording and that the message text is never included.
- **D5. Scope of the change.** Confirm that the reflection prompt change is the only LLM-facing change.

## Acceptance criteria (proposed)

- PB6-AC1: a clean-reflection tool failure yields task `failed`, run `success=False`.
- PB6-AC2: a successful tool result yields unchanged `completed` behaviour.
- PB6-AC3: the reflection input includes the tool name and error class for a failed attempt, and does not include the exception message.
- PB6-AC4: retry eligibility and counts are unchanged.
- PB6-AC5: retry still reuses the selected tool (PB-4 tests stay green).
- PB6-AC6: ToolMetrics records are unchanged.
- PB6-AC7: `execution_completed.success` matches the terminal result.
- PB6-AC8: no new persisted field, no schema change.
- PB6-AC9: a failed attempt followed by a successful final attempt follows D1.
- PB6-AC10: persistence failure isolation is unchanged.
- PB6-AC11: default-off and no-tool paths are unchanged where no tool failed.

## Red tests (write first, confirm red)

- Clean-reflection tool failure: currently `completed`, `success=True`. Must become failed.
- Reflection input omits the error message and includes name and class. Currently the reflection never sees the failure.
- Final-attempt success after an earlier tool failure (D1 case).
- `execution_completed.success` equals the task result.

## Sabotage checks (after implementation)

- S1: drop the failed-attempt check. Expect the clean-reflection test red.
- S2: include the exception message in the reflection input. Expect the redaction test red.
- S3: add a retry on tool failure. Expect the retry-count test red.
- S4: report `success` from reflection only. Expect the terminal-consistency test red.

## Not in this slice

Automatic failover. Retry eligibility changes. Persistence changes. Ranking changes. Production selection groups.
