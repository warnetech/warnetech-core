# PB-7A Task Verification Contract — Closure

**Status:** CLOSED. Implemented, tested, validated. PB-7 remains blocked only on its own verification design (PB-7 verification engine is not implemented).
**Approval:** spec §37 (`becea8a`, `8586b76`).

## Delivered
- `src/warnetech_core/agi/verification.py`: `VerificationCheckKind` (`execution_success`, `result_present`), `VerificationCheck`, `TaskVerificationContract` (frozen, `extra="forbid"`), `MAX_VERIFICATION_CHECKS = 2`, `parse_verification()`.
- `TaskNode.verification: TaskVerificationContract | None = None` (`planning.py`).
- Parser: absent/`null`/`{"checks": []}` → `None`. Malformed explicit contract → `ValueError` (count, unknown key, unknown or non-string kind, duplicate kind, wrong shape). No truncation.
- `orchestrator.run`: only the `decompose` call is wrapped in `except ValueError`; goal recorded `failed` through the existing `_persist` path, then the original error is re-raised.
- Tests: `tests/test_agi_task_verification_contract.py`, CONTRACT-001 to 016.

## Evidence
- RED: the test file failed at collection (`warnetech_core.agi.verification` did not exist). Not an assertion-level RED; recorded as such.
- GREEN: 16/16 PB-7A tests. Full suite `1078 passed, 2 skipped` (baseline 1062 + 16). `ruff check src tests` clean.
- Mutation checks (production file restored byte-identical after each):

| Sabotage | Tests that went red |
|---|---|
| S1 accept unknown kind | 008, 011, 015 |
| S2 remove count bound | 007 |
| S3 truncate overflow | 007 |
| S4 allow duplicate kinds | 006, 016 |
| S5 malformed explicit → `None` | 009 |
| S6 no failed-state cleanup | 015 |
| S7 swallow planning error | 015, 016 |

## Not done (by design)
- `required_field`, `equals`, `non_empty`, `result_type`, field references, expected values: PB-7B.
- Copilot read-only review: not run (not configured in this environment).
- No telemetry, no persistence change, no ToolMetrics, candidate, retry, failover, reflection or OutcomeAnalysis change.
- Codeflash: not run (no measured performance problem; standing policy).

## Baseline
Branch `ccr-2ddd16bf-wsxi86`. New clean baseline recorded at the closure commit.
