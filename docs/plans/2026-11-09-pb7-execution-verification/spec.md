---
status: PROPOSED
baseline: 162762a
---

# PB-7 Execution Verification

## Objective

Separate tool execution from task satisfaction. Today a tool that returns is treated as satisfying the task. PB-7 adds an explicit verification step between execution and reflection, so the system can ask whether the task was satisfied, not merely whether a tool returned.

Flow:

```text
tool selected -> tool execution -> execution result -> VERIFICATION -> reflection -> outcome analysis
```

## Non-goals

PB-7 does not retry, fail over, select another tool, change ToolMetrics ranking or accounting, change candidate eligibility, change production selection groups, change the persistent schema, revise plans, or create autonomous loops. It answers one question: did this execution satisfy the task?

## Proposed VerificationResult (bounded)

- `verified: bool`
- `reason: str` (bounded code, not free text)
- `evidence_count: int` (at least 0)
- `execution_valid: bool`

It must not contain private model reasoning or chain-of-thought. It records observable conclusions and bounded reasons only.

## Verification inputs and outputs

Inputs: the execution result of the attempt (tool name, success flag, bounded error class), and the task's success criteria. Output: a VerificationResult.

Terminal tool failure (execution_valid = false) can never produce verified = true.

## Acceptance criteria (proposed)

- PB7-AC1: a successful tool execution can still fail verification.
- PB7-AC2: a terminal execution failure can never verify successfully.
- PB7-AC3: verification never executes a tool.
- PB7-AC4: verification never modifies ToolMetrics.
- PB7-AC5: verification never alters candidate eligibility or ordering.
- PB7-AC6: verification never triggers a retry or failover.
- PB7-AC7: verification is deterministic for identical inputs.
- PB7-AC8: verification outcome is observable through telemetry without exposing exception text.
- PB7-AC9: no persistent schema change unless separately approved.
- PB7-AC10: all closed contracts remain green.

## Tests-first plan

1. Write a red test: a successful execution that fails verification is not reported as satisfied.
2. Write red tests for: terminal failure never verifies; verification never runs a tool; verification never touches ToolMetrics.
3. Write green controls: existing retry, selection, ToolMetrics and terminal-failure suites unchanged.
4. Confirm the red tests fail for the expected reason before any implementation.
5. Sabotage checks: force verified = true on terminal failure; let verification call a tool; let verification write ToolMetrics. Each must turn a required test red.

## Telemetry decision points

Open for review: whether verification adds a new event (verification_completed) or an optional field on an existing event. Default is no new event until a schema compatibility review approves it. The decision is recorded before implementation.

## Persistence decision

Proposed: no persistence. Verification results are transient per attempt. Any durable record requires a separate specification and a schema review.

## Risk analysis

- Verification could wrongly reject correct work (false negatives). Mitigation: bounded reason codes, and no automatic action on rejection.
- Verification could leak execution detail. Mitigation: no exception text, only error class.
- Verification could quietly become an authority over retry or selection. Mitigation: PB7-AC3 to PB7-AC6, enforced by tests.

## Rollback

Revert the PB-7 implementation commit. No state migration is needed, because nothing is persisted.

## Stop conditions

Stop and report if implementation requires: a persistent schema change, a new public API semantic, a retry or failover change, a ToolMetrics ranking change, a candidate change, a production selection group, a new LLM-facing prompt, or an unapproved telemetry event.

## Status

PROPOSED. Implementation is blocked until this specification is reviewed and approved.
