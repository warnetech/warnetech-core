# PB-7A Task Verification Contract — Specification

**Status:** PROPOSED. Implementation NOT APPROVED. Decisions D1–D15 await approval.
**Blocks:** PB-7 Execution Verification (`docs/plans/2026-11-09-pb7-execution-verification/spec.md`, SPEC APPROVED / IMPLEMENTATION BLOCKED).
**Scope of this document:** specification only. No production code, planner code, tests, persistence, or telemetry change.

---

## 1. Purpose

PB-7 can only return PASS or FAIL for a task if the task declares what must be true for it to succeed. Today no task does. `Goal.success_criteria` is free text, and `TaskNode.metadata` is an unstructured dict that the runtime never reads. Under PB-7 decision D1, every task without a contract returns UNCERTAIN with reason `no_verification_contract`, so PB-7 would add no PASS/FAIL capability until a structured contract exists.

PB-7A defines that structured, bounded, deterministic, planner-to-verifier contract. It defines only the data. PB-7 evaluates it later.

Lifecycle after PB-7A and PB-7:

```
Goal -> Planner -> TaskNode (+ optional TaskVerificationContract)
     -> Tool selection -> Execution -> PB-7 verification -> VerificationResult
     -> Reflection -> OutcomeAnalysis
```

---

## 2. Repository Inspection (basis for this proposal)

Facts below were read from the repository at HEAD `d5ee6f6`. Each one constrains the design.

| # | Area | Fact | Source | Consequence |
|---|------|------|--------|-------------|
| I1 | TaskNode fields | `id, title, description, status, depends_on, tool_hint, estimated_seconds, result: str \| None, metadata: dict[str, Any]`. Pydantic `BaseModel`. No verification field. | `src/warnetech_core/agi/planning.py:34-45` | A typed optional field fits the model. |
| I2 | TaskNode.metadata usage | No read or write of `.metadata` on a TaskNode anywhere in `src/` or `tests/`. | grep of `src/`, `tests/` | It is not an authority channel today. Using it for verification would create a shadow schema (spec §25). Rejected. |
| I3 | Planner prompt | `_PLANNER_SYSTEM` asks for `title, description, depends_on, tool_hint, estimated_seconds` and nothing else. | `planning.py` (`_PLANNER_SYSTEM`) | Adding a `verification` key is a prompt change (expected, §19). |
| I4 | Planner parser | `_parse_tasks` reads four keys, keeps `tool_hint` only when it is a `str`, drops non-dict entries, and ignores unknown keys. Bad JSON or a non-list logs an error and returns `[]`. `_seconds` rejects bool, non-finite and negative values. | `planning.py` (`_parse_tasks`, `_seconds`) | Existing convention is log-and-degrade. No exception is raised for malformed task output today. See D10. |
| I5 | Error hierarchy | No planning exception exists. `errors.py` defines `WarnetechError`, `ConfigError`, `ProviderError` and its subclasses, `AllProvidersFailed`, `ModelNotRegistered`, `PromptNotFound`, `PromptRenderError`, `MemoryError_`. | `src/warnetech_core/errors.py` | Spec §29 says "do not invent a new error hierarchy". Relevant to D10. |
| I6 | Result model | `TaskNode.result` is `str \| None`. The orchestrator sets it at `orchestrator.py:288` to `reflection.revised_output or output_text`. `output_text` is `reasoning.conclusion`, with `"Tool output: {last_tool.output}"` appended when a tool ran (line 233). `ToolResult.output` is `Any` (`tools.py:63`) and is not attached to the task. | `orchestrator.py:229-288`, `tools.py:57-67` | **Field-level checks cannot be evaluated against the stored task result.** A structured per-task result does not exist. See F1. |
| I7 | Persistence | `Plan`, `TaskNode`, `Goal` are never serialized by AGI code. Durable state is `goals.json` (`GoalRecord`), `reflections.json` (`ReflectionRecord`) and tool metrics. `plan_generated` telemetry carries only `{"task_count": n}`. | `state.py`, `tool_metrics.py`, `orchestrator.py:142` | **NO PERSISTENT SCHEMA IMPACT** for PB-7A, provided no plan persistence is added. Recorded per spec §26. Revisit if PB-9 adds plan persistence. |
| I8 | Goal.success_criteria | `list[str]` free text. Passed to the planner user prompt (`decompose`) and to the reflector as criteria (`orchestrator.py:239, 251`). | `planning.py`, `orchestrator.py` | Remains human/model-facing context (D12). Not parsed for verification. |
| I9 | Model conventions | Pydantic `BaseModel` is used for `TaskNode`, `Goal`, `Plan`, `ToolDefinition`, `ToolResult`, `ReflectionResult`. A frozen stdlib dataclass is used for `OutcomeAnalysis`. | `planning.py`, `tools.py`, `reflection.py`, `outcome.py` | Pydantic with `frozen=True, extra="forbid"` matches the planner-facing types. See D16. |
| I10 | Tests | `tests/test_agi_planning.py` has 16 tests. `_parse_tasks` is tested directly with raw JSON strings, which is the pattern for CONTRACT-013 and -014. | `tests/test_agi_planning.py` | Existing test style supports offline fixtures. |

### 2.1 Findings that change the proposal

**F1 — Field-level checks have no evaluable source today.** `required_field`, `equals`, `non_empty` with a field, and `result_type` need a structured result. The task carries only a string (I6). Adding a structured result would be a new runtime artefact, and its persistence would need its own decision. **PB-7A therefore limits the initial allowlist to checks evaluable against the current model (D4).** Field-level checks move to a follow-on slice, PB-7B, with its own specification.

**F2 — `result_present` needs a definition for a string result.** With `TaskNode.result: str | None`, "present" means a non-empty string after `strip()`. `None` and `""` both fail. This is the same as the whole-result form of `non_empty`, so `non_empty` is dropped from the initial allowlist as redundant.

**F3 — `execution_success` is a declaration, not a computation.** Its semantics belong to PB-7 and depend on PB-6 terminal-failure semantics. PB-7A only allows the planner to declare it. A contract that requires it is not evaluated by PB-7A.

**F4 — Malformed-contract handling is a real choice, not a detail.** Existing planner code degrades malformed task output to "no task" or "no list", without raising. Raising from `decompose` would change the orchestrator's behaviour for every malformed plan. See D10.

---

## 3. Objective

Define a bounded, typed, deterministic description of what must be true for a task to verify, using only:

- an allowlisted check kind,
- no fields, expressions or executable content,
- AND composition only,
- a hard upper bound on check count.

## 4. Non-goals

PB-7A does not:

- perform verification, or call any verifier,
- execute, retry, or fail over tools,
- select tools, change candidate resolution, `selection_group`, or ranking,
- change `ToolMetrics`, reflection, or `OutcomeAnalysis`,
- change any persistent schema or add plan persistence,
- emit telemetry,
- add an LLM verifier, HTTP verifier, database verifier, or any generated-code verifier,
- add production selection groups,
- evaluate `Goal.success_criteria` text.

## 5. Architectural principles

1. Verification requirements are explicit, typed, bounded, allowlisted, and deterministic.
2. `Goal.success_criteria` is never parsed at runtime to derive checks (D12).
3. `TaskNode.metadata` is not verification authority. The contract is a typed field (D1, D15).
4. Verification describes expected evidence. It never grants execution authority (no filesystem, network, code, or admin action).
5. A contract never names a tool, a retry, a fallback, or an alternative (§7).

---

## 6. Proposed model (adapted to repository conventions)

The conceptual model in the original proposal used a `field` and an `expected` value on each check. Both serve only the field-level and equality checks that F1 defers. They are **omitted from the PB-7A types entirely** rather than declared and rejected, so no latent surface exists.

```python
class VerificationCheckKind(str, Enum):
    EXECUTION_SUCCESS = "execution_success"
    RESULT_PRESENT = "result_present"


class VerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: VerificationCheckKind


class TaskVerificationContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    checks: tuple[VerificationCheck, ...] = Field(min_length=1, max_length=MAX_CHECKS)
    # MAX_CHECKS = 8 (D6)


class TaskNode(BaseModel):
    ...
    verification: TaskVerificationContract | None = None   # new, optional (D2)
```

Planner-facing JSON (new optional key, only when the planner chooses to emit it):

```json
{
  "title": "Generate the quarterly report",
  "description": "...",
  "depends_on": [],
  "tool_hint": "report_builder",
  "verification": {
    "checks": [
      {"kind": "execution_success"},
      {"kind": "result_present"}
    ]
  }
}
```

Notes:
- `tool_hint` and `verification` are independent keys. The parser never derives one from the other (CONTRACT-016).
- `Field(min_length=1)` is not the empty-contract rule. Empty input is normalised before construction (D3).
- Frozen models make the contract immutable after parse (PB7A-AC23).

---

## 7. Tool-hint independence and substitution

- A contract never encodes which tool must execute. `planner tool_hint -> candidate resolution -> selection` is unchanged.
- A contract should remain valid when an approved substitute executes, because both allowed kinds read only the task-level outcome (`execution_success`, `result_present`). This is the reason field-level checks are excluded for now: they would depend on tool-specific output shape (R3).
- Verification evaluates the outcome. It does not dictate selection.

---

## 8. Initial check allowlist

| Kind | Meaning | Evaluable from current model? | In PB-7A? |
|------|---------|-------------------------------|-----------|
| `execution_success` | Underlying execution completed without unresolved terminal failure (PB-6 semantics). | Declared only. Evaluated by PB-7. | Yes (D4) |
| `result_present` | `TaskNode.result` is a non-empty string after `strip()`. | Yes (F2). | Yes (D4) |
| `result_type` | Result matches an approved bounded type. | No. Needs structured result (F1). | No, PB-7B |
| `required_field` | Structured result contains a field. | No (F1). | No, PB-7B |
| `equals` | Bounded field equals a scalar. | No (F1). | No, PB-7B |
| `non_empty` | Non-empty value. Whole-result form duplicates `result_present`; field form needs F1. | Redundant / no (F2). | No |

Not permitted in any slice without a new specification: Python, shell, SQL, regex from the planner, generated expressions, `eval`, `exec`, dynamic import, module paths, callbacks, LLM verifier, HTTP verifier, database verifier.

---

## 9. `execution_success` (F3)

Declared, not computed, in PB-7A. PB-6 is authoritative for what "terminal failure" means. PB-7 defines how this check is evaluated against the run. PB-7A adds no field to record execution outcome on the task. Doing so would be a runtime state change and belongs to PB-7.

## 10. `result_present` (F2)

- `None` fails.
- `""` and whitespace-only strings fail.
- Any other string passes.
- Not applied to structured tool output, because none is attached to the task.

## 11–13. Field-level kinds (`result_type`, `required_field`, `equals`)

Deferred to PB-7B. A PB-7B specification must define: the structured per-task result record, whether it is persisted (and if so, a migration decision), the field-name grammar (single level, per original §31), the scalar value bounds (per original §32), and the string size bound, for which no precedent exists in the repository and which therefore requires approval before a limit is chosen (original §33).

## 14. `non_empty`

Removed from the PB-7A allowlist (F2). Reconsidered in PB-7B for field-level use.

---

## 15. Check composition

- ALL checks are required (AND only).
- PB-7 semantics (for reference; evaluated in PB-7, not PB-7A): PASS if every check passes; FAIL if at least one check demonstrably fails; UNCERTAIN if evidence needed for a check is unavailable.
- No OR, NOT, nesting, weights, or optional scoring.

## 16. Empty contract (D3)

`checks: []` must never mean PASS. The parser normalises an empty `checks` list to `verification = None`. A single representation of "no contract" then exists, and PB-7 returns UNCERTAIN / `no_verification_contract` for it.

## 17. Contract absence and compatibility

Missing `verification` key -> `None`. Existing planner output parses unchanged. No migration. Old fixtures remain valid (CONTRACT-013).

## 18. Planner responsibility

The planner may emit `verification` only with allowlisted kinds. It chooses approved check descriptors. It never writes verifier logic. It may use `Goal.success_criteria` as context when proposing checks, but the verifier never parses that text (D12).

## 19. Planner prompt change (expected)

`_PLANNER_SYSTEM` gains one sentence listing the two allowed `kind` values and stating that `verification` is optional. This is the only planner-facing change and is not implemented until approved.

## 20. Planner must not generate code

Restated for the parser: any key other than `title, description, depends_on, tool_hint, estimated_seconds, verification` is ignored. Within `verification`, only `checks` is read, and within each check only `kind` is read. Unknown keys inside `verification` or a check cause the whole contract to be rejected under D10. They are not silently ignored, because a silently ignored key could be a mistyped requirement.

---

## 21. Parser behaviour (D10, D15)

| Input | Result | Log |
|-------|--------|-----|
| `verification` absent or `null` | `None` | none |
| `checks` is `[]` | `None` (D3) | none |
| Valid, 1–8 checks, all allowlisted kinds | `TaskVerificationContract` | none |
| Unknown `kind` string | `None` for this task (whole contract rejected) | warning |
| Non-string or non-allowlisted value for `kind` | `None` for this task | warning |
| More than 8 checks | `None` for this task. **Never truncate**: dropping a required check would silently weaken the contract. | warning |
| Unknown key inside `verification` or a check | `None` for this task | warning |
| `verification` not a dict, or `checks` not a list | `None` for this task | warning |
| Contract error on one task | Other tasks in the plan are unaffected | — |

Under D10 (proposed option A), a malformed contract downgrades that task to `None`, so PB-7 reports UNCERTAIN. It never becomes PASS. The log line is the only record of the malformation, because PB-7A adds no telemetry.

---

## 22. Security

- Planner output is untrusted structured input.
- Validate `kind` against the enum, the check count, and the key set.
- No `eval`, `exec`, dynamic import, generated shell, or regex from contract data.
- A contract grants no execution authority (principle 4).

---

## 23. Proposed acceptance criteria

Adjusted from the original PB7A-AC1–28 to the reduced allowlist. Changed items are marked.

| ID | Criterion |
|----|-----------|
| PB7A-AC1 | Verification contract is optional on `TaskNode`. |
| PB7A-AC2 | Existing tasks without contracts remain valid. |
| PB7A-AC3 | Missing contract maps to PB-7 UNCERTAIN / `no_verification_contract`. |
| PB7A-AC4 | Empty contract (`checks: []`) does not imply PASS; it normalises to `None`. |
| PB7A-AC5 | Only allowlisted check kinds (`execution_success`, `result_present`) are accepted. |
| PB7A-AC6 | Unknown check kinds are rejected for that task. |
| PB7A-AC7 | Planner cannot provide executable verifier logic. |
| PB7A-AC8 | No field names exist in PB-7A types (changed: field references deferred, D7). |
| PB7A-AC9 | No expected values exist in PB-7A types (changed: value bounds deferred, D8). |
| PB7A-AC10 | Check count bounded at 8; over-limit contracts are rejected, never truncated. |
| PB7A-AC11 | Checks use AND semantics. |
| PB7A-AC12 | No nested logical expressions can be expressed. |
| PB7A-AC13 | No retry semantics in the contract. |
| PB7A-AC14 | No failover semantics in the contract. |
| PB7A-AC15 | No candidate-selection semantics in the contract. |
| PB7A-AC16 | No ToolMetrics semantics. |
| PB7A-AC17 | No telemetry emitted by PB-7A code. |
| PB7A-AC18 | No LLM verifier. |
| PB7A-AC19 | No generated-code verifier. |
| PB7A-AC20 | Malformed contracts fail safely (downgrade to `None`, never PASS). |
| PB7A-AC21 | Existing planner output remains parseable. |
| PB7A-AC22 | Valid new planner output parses deterministically. |
| PB7A-AC23 | Contract is immutable after parse (frozen models). |
| PB7A-AC24 | No persistent schema change. |
| PB7A-AC25 | No production selection groups added. |
| PB7A-AC26 | Existing candidate, ranking, retry and outcome contracts stay green. |
| PB7A-AC27 | Full test suite stays green. |
| PB7A-AC28 | `ruff check src tests` stays clean. |

---

## 24. Test plan (written after approval, tests first)

| ID | Test |
|----|------|
| CONTRACT-001 | Task without `verification` remains valid. |
| CONTRACT-002 | Missing contract parses to `None`. |
| CONTRACT-003 | Valid `execution_success` check parses. |
| CONTRACT-004 | Valid `result_present` check parses. |
| CONTRACT-005 | Two-check AND contract parses in declared order. |
| CONTRACT-006 | Unknown kind rejected for that task; other tasks unaffected. |
| CONTRACT-007 | `checks: []` normalises to `None`. |
| CONTRACT-008 | Nine checks rejected (not truncated); eight accepted. |
| CONTRACT-009 | Unknown key inside a check (e.g. `field`) rejected. |
| CONTRACT-010 | Non-string `kind` (object, number, bool) rejected. |
| CONTRACT-011 | Contract with a `field` key cannot express a field reference (covered by 009). |
| CONTRACT-012 | Planner text containing code or a dotted name cannot become a check. |
| CONTRACT-013 | Existing legacy planner fixture parses unchanged. |
| CONTRACT-014 | New structured planner fixture parses deterministically. |
| CONTRACT-015 | Contract is immutable after parse (assignment raises). |
| CONTRACT-016 | Contract parse does not alter `tool_hint`. |
| CONTRACT-017 | Contract parse does not alter `depends_on`. |
| CONTRACT-018 | Contract parse does not touch `ToolMetrics`. |
| CONTRACT-019 | Contract parse does not change `selection_group` or candidate resolution. |
| CONTRACT-020 | Malformed contract logs a warning and yields `None`; `decompose` does not raise. |
| CONTRACT-021 | `result_present` is False for `None`, `""` and whitespace-only results. |

Tests use the existing `_parse_tasks` pattern with raw JSON strings. No network, no model calls.

## 25. Sabotage plan (after implementation)

Each sabotage is applied to a backup copy, run against the targeted tests, then restored and verified byte-identical.

| ID | Sabotage | Expected |
|----|----------|----------|
| C1 | Map missing contract to PASS. | CONTRACT-002 / AC3 tests RED. |
| C2 | Accept unknown `kind` strings. | CONTRACT-006 RED. |
| C3 | Allow extra keys (`extra="ignore"`). | CONTRACT-009 RED. |
| C4 | Accept non-enum `kind` values (plain `str`). | CONTRACT-010 RED. |
| C5 | Remove the check-count bound. | CONTRACT-008 RED. |
| C6 | Truncate over-limit checks instead of rejecting. | CONTRACT-008 RED. |
| C7 | Derive `tool_hint` from `verification`. | CONTRACT-016 RED. |
| C8 | Normalise `checks: []` to a contract instead of `None`. | CONTRACT-007 RED. |

C1–C8 replace the original C1–C7. Original C3 (dotted field path) and C4 (callable expected value) no longer apply because those fields do not exist in PB-7A types.

---

## 26. Persistence review (recorded per spec §26)

- `TaskNode` persisted? **No** (I7).
- Planner output persisted? **No** (I7).
- Task structures written to a `schema_version: 1` record? **No.**
- Does adding `verification` alter a durable representation? **No.**

**Result: NO PERSISTENT SCHEMA IMPACT.** Re-check if plan persistence (PB-9) is introduced.

## 27. Backward compatibility

Verification is optional. A plan without it parses exactly as before. No caller reads `metadata` for verification, so nothing changes there.

---

## 28. Risks

| ID | Risk | Mitigation |
|----|------|-----------|
| R1 | Contract grows into an expression language. | Two-kind allowlist, AND only, no fields, no values, count bound. |
| R2 | Planner invents checks the tool cannot satisfy. | Checks are explicit and can FAIL or be UNCERTAIN in PB-7. Do not auto-weaken checks after failure. Evaluation harness (PB-8) measures contract quality. |
| R3 | Tool substitution changes output shape. | Only task-level kinds are allowed, so substitution does not change what is checked. Field checks deferred (F1). |
| R4 | Existing fixtures lack contracts. | Optional field; missing maps to `None`; PB-7 maps to UNCERTAIN. No migration. |
| R5 | Persistence drift if plans are stored later. | Persistence review recorded (§26); re-check at PB-9. |
| R6 | Silent downgrade hides planner errors from PB-7 (malformed looks like absent). | Warning log per malformed contract. Telemetry for this is out of scope and needs its own decision (D10 follow-up). |

## 29. Rollback

Remove the `verification` field, the parser branch, and the prompt sentence. Old planner output stays valid. PB-7 then returns UNCERTAIN for every task. No state migration. If rollback would require data conversion, scope has been exceeded and must stop.

## 30. Stop conditions

Stop and request review if PB-7A work requires:

- a persistent schema migration or a plan persistence format,
- a structured per-task result record (F1),
- any executable verification,
- any change to ToolMetrics, candidate resolution, `selection_group`, retry, failover, OutcomeAnalysis or ReflectionResult,
- an LLM, HTTP or database verifier,
- production selection groups,
- an expression language of any depth.

---

## 31. Design decisions requiring approval

| ID | Decision | Proposal |
|----|----------|----------|
| D1 | Separate typed `TaskVerificationContract`, not `metadata`. | YES. Typed optional field on `TaskNode`. |
| D2 | Contract optional. | YES. |
| D3 | Missing or empty contract. | Both normalise to `None`. PB-7 returns UNCERTAIN / `no_verification_contract`. |
| D4 | Initial check kinds. | **Changed from original.** `execution_success` and `result_present` only. `result_type`, `required_field`, `equals`, `non_empty` deferred (F1, F2). |
| D5 | Composition. | AND only. |
| D6 | Maximum checks. | 8 per task. Over-limit rejected, never truncated. |
| D7 | Field references. | **Moot for PB-7A.** No field references exist. Field grammar deferred to PB-7B (single-level). |
| D8 | Expected values. | **Moot for PB-7A.** No expected values exist. Value bounds deferred to PB-7B. |
| D9 | Planner executable verifier logic. | FORBIDDEN. |
| D10 | Malformed contract handling. | **Option A (proposed):** downgrade that task's contract to `None` with a warning log; `decompose` does not raise. **Option B (alternative):** raise a planning error from `decompose`. Option B needs a new exception and changes orchestrator behaviour for malformed plans. A is recommended because existing planner code already degrades malformed output (I4, I5). |
| D11 | Persistence. | No persistent schema change in PB-7A. |
| D12 | `Goal.success_criteria`. | Remains free text. Planner may use it as context. Verifier never parses it. |
| D13 | Warning log for malformed contracts. | YES. Log only, no telemetry event, no new event name. |
| D14 | Planner prompt change. | YES, one sentence in `_PLANNER_SYSTEM`. Expected per §19. |
| D15 | Typed field instead of `metadata`. | YES. `metadata` stays unused by verification. |
| D16 | Model type. | Pydantic `BaseModel` with `frozen=True, extra="forbid"`, matching `TaskNode` and `ToolResult`. Not a dataclass. |

---

## 32. Ready gate

PB-7A implementation remains blocked until:

- [ ] D1–D16 approved (or amended and re-approved),
- [x] TaskNode inspected (I1, I2),
- [x] Planner output schema inspected (I3),
- [x] Planner parser inspected (I4),
- [x] Planner error conventions inspected (I5),
- [x] Planner tests inspected (I10),
- [x] Result model inspected (I6, F1),
- [x] Persistence impact inspected (I7, §26),
- [ ] Check kinds finalised (D4 approval),
- [ ] Bounds finalised (D6; D7/D8 moot),
- [ ] Malformed-contract behaviour finalised (D10 approval),
- [ ] PB-7 specification reconciled with this allowlist (see §33).

## 33. Dependency on PB-7

PB-7 spec D1 (absent contract → UNCERTAIN) is unchanged by this proposal. PB-7 check semantics must be reconciled with the PB-7A allowlist before PB-7 implementation. Any PB-7 check that needs a field or an expected value is out of scope until PB-7B. This document does not edit the PB-7 spec.

---

## 34. Validation for this documentation change

- `git diff --check` clean.
- No source, test, or schema file changed.
- No tests added. Tests for this contract are written only after approval.

## 35. Next decision

Approval of D1–D16, with particular attention to **D4** (reduced allowlist), **D10** (malformed-contract option A or B), and **D16** (pydantic model). After approval, the next step is the PB-7A implementation specification's test-first plan. Implementation does not start until that approval.
