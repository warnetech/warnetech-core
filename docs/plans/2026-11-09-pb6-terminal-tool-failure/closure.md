---
status: closed
baseline: 2226e8c
---

# PB-6 closure: terminal tool failure semantics

## Commits

- Proposal: `2226e8c`
- Implementation: `28b60aa` `fix(agi): terminal tool failure fails the task and run (PB-6)`
- Decisions and telemetry inspection: `15b3a5a`, `14bf7e4`
- Controls: `8285db0`

## Files changed

- `src/warnetech_core/agi/orchestrator.py`: terminal-failure detection, bounded reflection line, guaranteed `has_errors` on terminal failure.
- `src/warnetech_core/agi/tools.py`: `ToolResult.error_type` (exception class name, not persisted).
- `tests/test_agi_terminal_tool_failure.py`: 10 tests.
- Docs: PB-6 spec, `docs/AGI.md`.

## Tests before the fix

Run against `2226e8c` in a temporary worktree, with that worktree's `src` on the path:

- RED (4): terminal failure reported as success; reflection without failure context; executed tool not named; `execution_completed` reports success.
- GREEN controls (4): exception message excluded; recovered failure succeeds; no new attempt; no failover.

## Implementation

- After the reflection, if the attempt's tool failed and no retry follows under the existing rule, the reflection is re-run with `Tool failed: <tool> (<ErrorClass>)`.
- If that reflection still has no errors, an ERROR critique with the same line is added. Existing `has_errors` handling then fails the task.
- Retry rules, retry counts, selection, ToolMetrics and persistence are unchanged.

## Telemetry decision

A: reuse the existing `execution_completed.success`. No new field or event. The exception message is not in telemetry.

## Results

- Terminal failure: `success=False`.
- Reflection context: `Tool failed: <tool> (<ErrorClass>)`. Exception message excluded.
- Recovered retry: the task can still succeed. The reflection gets no failure line.
- No new retry. Retry reuses the originally selected tool (PB-4).
- Executed identity: the failure names the tool that ran.
- No failover: the alternative candidate never runs.
- ToolMetrics: one failure per attempt, no extra terminal record.
- Persistence: `persistence_failed` still emitted separately; no schema change.

## Sabotage

- TF1 remove terminal propagation: 4 failed.
- TF2 include exception message: 3 failed.
- TF3 treat intermediate failure as terminal: 1 failed.
- TF4 extra retry on failure: 3 failed.
- TF5 attribute failure to hint: 1 failed.
- TF6 failover after terminal failure: not run as a separate sabotage. Covered by the no-failover control and PB-4 reselection sabotage.
- TF7 report success regardless: 2 failed.

Each sabotage was restored and checked for byte-identical content.

## Validation

- 1062 passed, 2 skipped.
- `ruff check src tests`: clean.
- `ruff check .` reports issues in `examples/` and `scripts/`, which predate PB-6 and are outside the baseline lint scope.
- Type check: not configured in this repository.

## Copilot review

NOT RUN. The local Copilot reviewer is not configured in this environment.

## Follow-up

- `tools.py` still logs the exception message at error level. Outside PB-6 scope.
- A terminal failure costs one extra reflection call.
- Lint cleanup in `examples/` and `scripts/` is separate work.

## Working tree

Clean and synced with origin at closure time.
