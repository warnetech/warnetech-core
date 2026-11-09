# PB-7A Task Verification Contract — Specification

**Status:** CLOSED (see closure.md). Implemented at the closure commit.
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

## 31. Design decisions requiring approval (D1–D12)

Status values: PROPOSED (user table, unchanged) and the inspection verdict in §31.1. No decision is approved by this section.

| ID | Decision | Proposed PB-7A Contract | Status |
|---|---|---|---|
| **D1** | Structured task verification contract | Introduce a separate typed `TaskVerificationContract` containing machine-verifiable task completion criteria. | **PROPOSED** |
| **D2** | Backwards compatibility | The verification contract remains optional. Existing tasks without a contract continue to parse and execute normally. | **PROPOSED** |
| **D3** | Missing or empty contract | Treat as no usable verification contract. PB-7 returns `UNCERTAIN` with reason `no_verification_contract`. An empty contract must never automatically mean PASS. | **PROPOSED** |
| **D4** | Initial verification check kinds | Initially allow only `execution_success`, `result_present`, `result_type`, `required_field`, `equals`, and `non_empty`. | **PROPOSED** |
| **D5** | Check composition | Use **AND-only** semantics initially. Every declared required check must pass for overall PASS. No OR, NOT, weighting, or nested logical expressions. | **PROPOSED** |
| **D6** | Maximum checks | Limit each task to **8 verification checks** initially. | **PROPOSED** |
| **D7** | Field references | Permit **single-level fields only** initially. No arbitrary dotted paths, indexes, deep object traversal, dynamic attributes, or property execution. | **PROPOSED** |
| **D8** | Expected values | Permit bounded safe scalar values only: `None`, boolean, integer, finite float, and bounded string. No mappings, lists, binary values, callables, executable objects, or arbitrary structured payloads. | **PROPOSED** |
| **D9** | Executable verifier logic | **FORBIDDEN.** Planner output cannot contain Python, shell commands, `eval`, `exec`, callbacks, dynamic imports, generated predicates, or any other executable verification logic. | **PROPOSED** |
| **D10** | Malformed contracts | Fail safely using the repository's existing planning-domain error convention. Unknown kinds, unsafe values, malformed structures, invalid fields, and excessive checks must never silently become PASS. | **PROPOSED** |
| **D11** | Persistence | PB-7A is not permitted to change the persistent schema. If adding the contract affects durable task/plan representation or `schema_version`, implementation stops for a separate migration decision. | **PROPOSED** |
| **D12** | Relationship to `Goal.success_criteria` | Keep `Goal.success_criteria` as free-form human/model-facing context. The planner may use it when producing an allowlisted structured verification contract, but PB-7 must **not** parse that free text at runtime to invent verification checks. | **PROPOSED** |

### 31.1 Repository inspection verdicts

Each verdict cites repository evidence. Inspection did not modify production code.

| ID | Verdict | Evidence and reasoning |
|---|---|---|
| D1 | **APPROVE** | `TaskNode` is a pydantic model (`planning.py:34`) with no verification field. A typed optional field fits it. `metadata` is never read or written (grep of `src/`, `tests/`). |
| D2 | **APPROVE** | `_parse_tasks` already tolerates missing keys; `verification` absent maps to `None`. |
| D3 | **APPROVE** | Normalise `checks: []` to `None`. Matches the rule that an empty contract never means PASS. |
| D4 | **AMEND** | Only `execution_success` and `result_present` are evaluable. `TaskNode.result` is `str \| None` (`planning.py:43`), set at `orchestrator.py:288` from `reasoning.conclusion` plus stringified `last_tool.output`. `ToolResult.output` is `Any` (`tools.py:63`) and never attached to the task. `result_type`, `required_field`, `equals`, `non_empty`-on-field have no structured source. **Amend to two kinds.** |
| D5 | **APPROVE** | AND-only matches the runtime's boolean checks. No composition code exists to conflict with. |
| D6 | **AMEND** | No check-count precedent exists. The only count bounds are orchestrator constructor defaults (`max_cycles=20`, `max_retries_per_task=2`, `orchestrator.py:86-87`); the planner has no task cap. With two boolean kinds, a cap of 8 admits only duplicates. **Amend to: reject duplicate kinds, and set the ceiling equal to the allowlist size.** A numeric ceiling is set when PB-7B adds kinds. |
| D7 | **APPROVE for PB-7B only; MOOT for PB-7A** | PB-7A contains no field references. `ToolDefinition.parameters` is a free-form `dict[str, Any]` (`tools.py:27`) with no name grammar, so there is no repository precedent for a field grammar. Single-level remains the proposal for PB-7B. |
| D8 | **AMEND / DEFER** | PB-7A has no expected values, so D8 is moot. No string-size bound exists in AGI code; the only truncation is for log text (`text[:200]`, `planning.py:176`, `reflection.py:141`). Spec §33 requires approval before a limit is chosen. Scalar bounds defer to PB-7B. |
| D9 | **APPROVE** | Planner output is parsed as JSON and never executed. Forbidding executable logic keeps it that way. |
| D10 | **AMEND** | The stated convention does not exist. No planning exception exists (`errors.py` has no planning type). AGI parsers never raise: `_coerce.py` documents that helpers "normalise such input so the AGI parsers never raise", and `_parse_tasks` logs and returns `[]` (`planning.py:176`). **Amend to option A: downgrade the task's contract to `None` with a warning log; `decompose` does not raise.** Option B (raise) would introduce a new error type and change orchestrator behaviour. |
| D11 | **APPROVE** | `TaskNode`, `Plan` and `Goal` are not serialised anywhere in `src/` (only exported in `__init__.py` and used in memory). Durable state is `GoalRecord`, `ReflectionRecord` and tool metrics only. No `schema_version: 1` structure is touched, so **PERSISTENCE MIGRATION REVIEW is not required.** |
| D12 | **APPROVE** | `Goal.success_criteria` is `list[str]` (`planning.py:65`), used only as planner prompt text and reflection criteria (`orchestrator.py:239, 251`). Nothing parses it into checks. |

### 31.2 Inspection findings by area

- **TaskNode:** pydantic model, fields `id, title, description, status, depends_on, tool_hint, estimated_seconds, result, metadata`. Not persisted. Verification field addition is type-safe.
- **Planner schema and prompt:** `_PLANNER_SYSTEM` (`planning.py:103-111`) lists four keys. The `verification` key would be a one-sentence prompt change (D14).
- **Parser:** reads only known keys; drops non-dict entries; `tool_hint` kept only if `str`; log-and-degrade on bad input, never raises.
- **Result model:** string-only at task level. Structured output exists only in `ToolResult.output` and is not connected to a task. This is the reason D4 and D8 are amended.
- **Persistence:** no task or plan serialisation. Telemetry `plan_generated` carries `{"task_count": n}` only.
- **Error contract:** no planning exception exists. Existing convention is no-raise (D10).
- **Tests:** `tests/test_agi_planning.py` (16 tests) calls `_parse_tasks` with raw strings; sufficient fixture pattern for CONTRACT-013/014.
- **Backwards compatibility:** optional field plus no-raise parser keeps legacy output parsing unchanged.

### 31.3 Summary of verdicts

| ID | Verdict |
|----|---------|
| D1 | APPROVE |
| D2 | APPROVE |
| D3 | APPROVE |
| D4 | AMEND: `execution_success`, `result_present` only |
| D5 | APPROVE |
| D6 | AMEND: reject duplicate kinds; ceiling equals allowlist size |
| D7 | MOOT for PB-7A; single-level for PB-7B |
| D8 | MOOT for PB-7A; defer scalar and string bounds to PB-7B |
| D9 | APPROVE |
| D10 | AMEND: option A (downgrade to `None`, warning log, no raise) |
| D11 | APPROVE; no migration review required |
| D12 | APPROVE |

## 32. Approval gate

### Decision approval

- [ ] D1 approved
- [ ] D2 approved
- [ ] D3 approved
- [ ] D4 approved (as amended in §31.3)
- [ ] D5 approved
- [ ] D6 approved (as amended in §31.3)
- [ ] D7 approved (moot for PB-7A; PB-7B only)
- [ ] D8 approved (moot for PB-7A; deferred)
- [ ] D9 approved
- [ ] D10 approved (option A)
- [ ] D11 approved
- [ ] D12 approved

### Repository inspection

- [x] TaskNode model inspected
- [x] Planner output schema inspected
- [x] Planner prompt inspected
- [x] Planner parser inspected
- [x] Planner tests inspected
- [x] Task/plan persistence impact inspected
- [x] Existing serialisation paths inspected
- [x] Existing planning-domain error type confirmed: **none exists**

### Contract compatibility

- [ ] Initial check kinds confirmed against result models (requires D4 amendment)
- [ ] Check-count bound reviewed (requires D6 amendment)
- [ ] Single-level field references: deferred to PB-7B
- [ ] Scalar-value restrictions: deferred to PB-7B
- [ ] String-size bound: no repository precedent; requires approval before any value is chosen (§33)
- [ ] Malformed-contract behaviour defined (requires D10 approval)
- [x] Existing planner fixtures remain backwards-compatible (by design; verified at implementation)
- [x] No persistent schema migration required

### Current status

```text
PB-7:    SPEC APPROVED / IMPLEMENTATION BLOCKED BY PB-7A
PB-7A:   PROPOSED
TaskVerificationContract: NOT IMPLEMENTED
Planner output changes:   NOT APPROVED
TaskNode changes:         NOT APPROVED
PB-7 production verification: NOT IMPLEMENTED
```

## 32.1 Report for approval

```text
PB-7A D1:  APPROVE
PB-7A D2:  APPROVE
PB-7A D3:  APPROVE
PB-7A D4:  AMEND (execution_success, result_present only)
PB-7A D5:  APPROVE
PB-7A D6:  AMEND (reject duplicates; ceiling = allowlist size)
PB-7A D7:  MOOT for PB-7A (single-level for PB-7B)
PB-7A D8:  MOOT for PB-7A (deferred to PB-7B)
PB-7A D9:  APPROVE
PB-7A D10: AMEND (option A: downgrade to None, warning, no raise)
PB-7A D11: APPROVE (no migration review required)
PB-7A D12: APPROVE

TaskNode findings:      pydantic model; no verification field; metadata unused; not persisted.
Planner schema findings: four keys in prompt; verification key = one-sentence prompt change.
Parser findings:        ignores unknown keys; log-and-degrade; never raises.
Result-model findings:  TaskNode.result is str; ToolResult.output (Any) not attached to task.
Persistence findings:   TaskNode/Plan/Goal not serialised; durable records unaffected.
Error-contract findings: no planning exception exists; AGI convention is no-raise.
Backwards-compatibility findings: optional field; missing key -> None; legacy fixtures unchanged.

PB-7A ready for approval: YES (decisions only; implementation still blocked)
Blockers:
  - D4, D6, D10 amendments require user approval
  - No string-size bound precedent (spec §33); needed only in PB-7B
  - PB-7 spec check semantics must be reconciled with the final allowlist
```


## 33. Dependency on PB-7

PB-7 spec D1 (absent contract → UNCERTAIN) is unchanged by this proposal. PB-7 check semantics must be reconciled with the PB-7A allowlist before PB-7 implementation. Any PB-7 check that needs a field or an expected value is out of scope until PB-7B. This document does not edit the PB-7 spec.

---

## 34. Validation for this documentation change

- `git diff --check` clean.
- No source, test, or schema file changed.
- No tests added. Tests for this contract are written only after approval.

## 35. Next decision

Approval of D1–D16, with particular attention to **D4** (reduced allowlist), **D10** (malformed-contract option A or B), and **D16** (pydantic model). After approval, the next step is the PB-7A implementation specification's test-first plan. Implementation does not start until that approval.

---

## 36. PB-7A final decision amendments (D4, D6, D10)

Specification only. No production code, tests, planner, `TaskNode`, persistence or telemetry changed.

**PB-7 status:** SPEC APPROVED / IMPLEMENTATION BLOCKED BY PB-7A.
**PB-7A status:** SPEC UNDER REVIEW / IMPLEMENTATION BLOCKED.

### 36.1 Updated decision table

| ID | Decision | Status |
|----|----------|--------|
| D1 | Structured typed `TaskVerificationContract` | APPROVED |
| D2 | Optional contract; backwards compatible | APPROVED |
| D3 | Missing or empty usable contract → PB-7 UNCERTAIN / `no_verification_contract` | APPROVED |
| D4 | Initial check kinds (amended) | AMENDED. `execution_success` APPROVED; `result_present` APPROVED; `required_field`, `equals`, `non_empty`, `result_type` **DEFERRED** (§36.2). |
| D5 | AND-only composition | APPROVED |
| D6 | Bounded check count (amended) | AMENDED. Hard maximum APPROVED. Exact value **derived in §36.3 as 2**. Silent truncation FORBIDDEN. |
| D7 | Single-level field references | APPROVED for PB-7B only, subject to result-model compatibility. **Moot for PB-7A** (no field references). |
| D8 | Bounded safe scalar expected values | APPROVED for PB-7B only, subject to repository value/string bounds. **Moot for PB-7A.** |
| D9 | Executable verifier logic | FORBIDDEN (approved) |
| D10 | Malformed contract handling (amended) | AMENDED (§36.4). Explicit malformed contract → planning/parsing failure. Missing → `None`. Explicit empty → no usable contract. Malformed must not become UNCERTAIN. |
| D11 | No persistent schema change | APPROVED. Inspection confirms no migration (§31.2). |
| D12 | `Goal.success_criteria` remains free-form context | APPROVED |

### 36.2 D4 inspection report

Governing finding: the execution result that a task holds is a string. `TaskNode.result: str | None` (`planning.py:43`), set at `orchestrator.py:288` to `reflection.revised_output or output_text`. `output_text` is `reasoning.conclusion`, plus `"Tool output: {last_tool.output}"` when the last tool succeeded and its output is truthy (`orchestrator.py:229-234`). That is the only structured-to-string conversion, and it discards structure. The structured value exists only as `ToolResult.output: Any` (`tools.py:63`), set from the handler's return (`tools.py:129-135`). Handler return types are unconstrained: each tool defines its own output shape.

**execution_success — APPROVE.** Declarative. PB-7 evaluates it from PB-6 terminal-failure semantics. No result-shape dependency.

**result_present — APPROVE.** Definition: `TaskNode.result` is a string, and `result.strip() != ""`. `None` and whitespace-only fail. Supported by the current model with no new state.

**required_field — DEFER.**
- Supported by current result model: NO.
- Evidence: the task holds a string, so no field exists. The structured `ToolResult.output` is `Any`, and each handler chooses its shape (`tools.py:129`), so a field check would need tool-specific extraction code. That violates the D4 conditions.
- Implementation complexity: high. Needs either a structured per-task result record (new state, with persistence question) or a per-tool adapter.
- Recommended: DEFER to PB-7B.

**equals — DEFER.**
- Supported: NO, for the same reason. Comparing a field to a scalar requires field access on `Any` output.
- Expected scalar types if later approved: `None`, `bool`, `int`, finite `float`, bounded `str` (D8).
- Recommended: DEFER to PB-7B.

**non_empty — DEFER.**
- Whole-result form: supported, but identical to `result_present`. Adding it adds no capability.
- Field form: NO, same as `required_field`.
- Supported value types for the whole-result form: `str` only.
- Recommended: DEFER. Do not add a redundant kind.

**result_type — DEFER.**
- Supported on the task result: effectively meaningless. `TaskNode.result` is always `str` or `None`, so only the `string` type is reachable.
- Supported on `ToolResult.output`: technically possible with an `isinstance` check against allowlisted types, but `ToolResult` is not attached to the task and is not in the verifier's input path today.
- Safe allowlisted types if later approved: `str`, `int`, `float`, `bool`, `dict`, `list`. No class paths.
- Recommended: DEFER to PB-7B, with the structured result record.

**D4 final:** PB-7A allowlist = `{execution_success, result_present}`. Every conditional kind fails the D4 condition "supported by the current result model without tool-specific verifier code". None is approved now.

**Unknown kinds** remain rejected (§21, D10). No tool-specific kinds (`file_exists`, `http_status`, `database_row_exists`, `command_succeeds`, `process_running`, `service_available`) are added.

### 36.3 D6 inspection report

**Existing relevant bounds:**
- Planner output token cap: `max_tokens=2048` for the entire plan response (`planning.py`, `decompose`). This bounds total plan size, not per-task checks.
- Planner task count: no explicit cap in code.
- Orchestrator: `max_cycles=20`, `max_retries_per_task=2` (`orchestrator.py:86-87`). These bound execution, not contract size.
- Service text bounds: `MAX_QUESTION_CHARS = 4000`, `MAX_UPLOAD_CHARS = 1_000_000` (`service/warnetech_ui.py:32-33`). These are the only string/collection bounds on untrusted input in the repository.
- Gateway fallback bound: `max_fallbacks=3`.
- No existing per-collection cap on planner-produced lists (`depends_on`, `tool_hint`, `estimated_seconds` are unbounded).

**Typical planned task shape:** the planner emits `title, description, depends_on, tool_hint, estimated_seconds`. Test fixtures use 1–3 tasks. No fixture contains verification material.

**Typical verification-condition need:** a task needs at most one condition per allowed kind, because the checks are boolean, AND-combined, and every PB-7A kind is a single property of one task result.

**Existing schema/list precedent:** none for a per-task check list.

**Derivation:** with AND-only composition and two boolean kinds, a conjunction can contain each kind at most once. Duplicate kinds add no information. Therefore the maximum number of meaningful distinct checks equals the size of the allowlist.

**Recommended:** `MAX_VERIFICATION_CHECKS = len(VerificationCheckKind) = 2`, defined once, centrally, as a derived constant rather than a literal. Duplicate kinds are rejected. The overall contract is rejected if it exceeds `MAX_VERIFICATION_CHECKS` distinct checks, which cannot occur for PB-7A but is enforced for when PB-7B adds kinds.

**Why this value:** it is derived from the allowlist, not chosen. It rejects duplicates rather than truncating. It rises only through a new specification that adds kinds. The 8 previously proposed had no evidence behind it.

**Overflow behaviour:** the contract is rejected under D10. Never truncated, never sliced, never randomly selected. Duplicate kinds count as overflow.

**Relation to the 2048-token plan cap:** a two-check contract adds a bounded amount to plan size (each check is one small JSON object), so the plan-level cap is not affected.

### 36.4 D10 inspection report

**Existing planner-domain exception:** NONE. `errors.py` defines `WarnetechError` (base), `ConfigError`, `ProviderError` and subclasses, `RateLimitError`, `TimeoutError_`, `AllProvidersFailed`, `ModelNotRegistered`, `PromptNotFound`, `PromptRenderError`, `MemoryError_`. None describes planner or task output.

**Parser validation path today:** `_parse_tasks` (`planning.py`) logs and returns `[]` for invalid JSON or a non-list. It drops non-dict entries. It keeps `tool_hint` only if it is a `str`. It never raises.

**Malformed planner output behaviour today:** silent degradation. Bad JSON yields an empty plan. `orchestrator.run` (line 141) calls `decompose` with no guard. An empty plan ends the goal as failed (`orchestrator.py:143-150`).

**Other AGI validation convention:** `outcome.py` raises `ValueError` for an invalid confidence (strict validation, no clamping). `tool_metrics.py` raises `ValueError` for invalid prior pseudo-counts and negative latency. So AGI validation of invalid input uses `ValueError`.

**Recommended exception for an invalid verification contract:** `ValueError`, following the AGI convention in `outcome.py` and `tool_metrics.py`. Raised from the contract parser, with a message naming the task and reason.

**Error message format:** `"invalid verification contract for task <task_id>: <reason>"`, where `<reason>` is a fixed code from a closed set: `unknown_kind`, `duplicate_kind`, `too_many_checks`, `unknown_key`, `wrong_type`, `not_a_mapping`. The message must not include the raw planner text, the model response, prompts, or any credential-bearing content (§10 of the original spec, and D10 safety rule).

**Does this require a new exception:** NO. `ValueError` is the narrowest accurate existing type, and `WarnetechError` is too broad.

**Consequence the approver must accept:** `decompose` would now raise `ValueError` for a malformed explicit contract, and `orchestrator.run` does not catch it. Two effects follow:
1. The goal aborts before any execution, which is the requested behaviour.
2. `run` has already recorded the goal as `"running"` (`orchestrator.py:131`) and does not record a terminal status on exception. The goal record would stay `running`. Choosing between (a) catching the `ValueError` in `run` and recording `"failed"`, and (b) leaving it uncaught, is a decision (D10-a below). Recommended: (a), a narrow `except ValueError` around `decompose` that records `"failed"` and re-raises or returns a failed result. This is an orchestrator change and must be specified and approved before PB-7A implementation.

**Absent versus malformed:** `verification` absent or `null` → `None` (no failure). `checks: []` explicitly → `None` with no failure (D3). Any other invalid material → `ValueError` (planning failure).

### 36.5 Consequence of D10 on D3

D3 is unchanged: a usable-contract absence still yields PB-7 UNCERTAIN. An explicit empty contract is treated as absence, not as malformed. This is the only exception to the fail-closed rule, and it cannot yield PASS.

### 36.6 Final approval gate

PB-7A becomes ready for implementation only when:

- [ ] D1 approved
- [ ] D2 approved
- [ ] D3 approved
- [ ] D4 final allowlist approved: `execution_success`, `result_present`; other four deferred
- [ ] D5 approved
- [ ] D6 exact maximum approved: `MAX_VERIFICATION_CHECKS = 2`, derived; duplicates rejected
- [ ] D7 confirmed against result models (moot for PB-7A; deferred to PB-7B)
- [ ] D8 confirmed against value/string bounds (moot for PB-7A; deferred to PB-7B)
- [ ] D9 approved
- [ ] D10 approved: `ValueError`, fail at planning, closed reason codes, no raw content
- [ ] D10-a approved: orchestrator behaviour on `ValueError` from `decompose` (recommended: catch, record `failed`)
- [ ] D11 confirmed: no schema migration
- [ ] D12 approved

Inspection:

- [x] TaskNode inspected
- [x] planner prompt inspected
- [x] planner output format inspected
- [x] parser inspected
- [x] planner-domain errors inspected (none exists)
- [x] planner tests inspected
- [x] execution-result models inspected
- [x] serialisation inspected
- [x] persistence impact inspected
- [ ] backward compatibility confirmed by approval

### 36.7 Report

```text
PB-7A status: SPEC UNDER REVIEW / IMPLEMENTATION BLOCKED
PB-7 status:  SPEC APPROVED / IMPLEMENTATION BLOCKED BY PB-7A

D4 final recommendation:
  execution_success: APPROVE
  result_present:    APPROVE
  required_field:    DEFER
  equals:            DEFER
  non_empty:         DEFER
  result_type:       DEFER

D6:
  Existing bounds found: planner max_tokens=2048; orchestrator max_cycles=20,
    max_retries_per_task=2; service MAX_QUESTION_CHARS=4000, MAX_UPLOAD_CHARS=1_000_000.
    No per-task check precedent.
  Recommended maximum: MAX_VERIFICATION_CHECKS = 2 (derived = allowlist size)
  Evidence: AND-only + boolean kinds => each kind at most once.
  Overflow behaviour: reject as planning failure. Never truncate.

D10:
  Existing planner exception: none
  Selected error behaviour: raise ValueError from contract parser; decompose propagates
  New exception needed: NO
  Open consequence: orchestrator.run leaves goal recorded as "running" on uncaught error (D10-a)

TaskNode findings:        pydantic, no verification field, metadata unused, result is str
Planner prompt findings:  four keys; verification = one-sentence addition
Parser findings:          log-and-degrade today; ValueError path would be new
Planner test findings:    16 tests; raw-string fixtures; no verification fixtures
Execution-result findings: ToolResult.output Any, not attached to task; task result is str
Serialisation findings:   none of TaskNode/Plan/Goal serialised
Persistence findings:     no schema impact
Backward-compatibility:   absent key -> None; legacy fixtures unchanged; run() behaviour
                          changes only for malformed explicit contracts (new)

Production code changed: NO
Tests changed:           NO
Persistent schema changed: NO
Working tree: spec change only, pending commit
Branch: ccr-2ddd16bf-wsxi86 (upstream origin/ccr-2ddd16bf-wsxi86)

PB-7A ready for final approval: NO (approval of D4, D6, D10, D10-a required; then final gate)
Remaining blockers:
  - D10-a orchestrator behaviour on ValueError
  - D7/D8 remain PB-7B items; their moot status for PB-7A needs confirmation
  - No string-size bound convention for planner text beyond service constants; not needed in PB-7A
```

---

## 37. PB-7A final approval record

**PB-7A:** APPROVED FOR TESTS-FIRST IMPLEMENTATION. Governance baseline `becea8a`.
**PB-7:** SPEC APPROVED / IMPLEMENTATION BLOCKED until PB-7A is implemented, tested and closed.

### 37.1 Final decisions

| ID | Decision |
|----|----------|
| D1 | Typed `TaskVerificationContract`. APPROVED. |
| D2 | Optional contract; backwards compatible. APPROVED. |
| D3 | Absent or explicit-empty contract → PB-7 `UNCERTAIN` / `no_verification_contract`. Empty never means PASS. APPROVED. |
| D4 | Two kinds only: `execution_success`, `result_present`. `required_field`, `equals`, `non_empty`, `result_type` DEFERRED to PB-7B. APPROVED. |
| D5 | AND-only. APPROVED. |
| D6 | `MAX_VERIFICATION_CHECKS = 2`. Duplicate kinds rejected. Overflow rejected. Never truncated, never deduplicated silently. APPROVED. |
| D7 | Field references DEFERRED to PB-7B. No `field` key in PB-7A model. |
| D8 | Expected values DEFERRED to PB-7B. No `expected` key in PB-7A model. |
| D9 | Executable verifier logic FORBIDDEN. APPROVED. |
| D10 | Malformed explicit contract raises `ValueError` during planning/parsing. No new exception class. Message names the bounded violation only. APPROVED. |
| D10-a | On planning failure the goal must not remain `running`. Record `failed` (existing state, `orchestrator.py:150`), then re-raise the original `ValueError`. Catch narrowly around the `decompose` call only. APPROVED with that boundary. |
| D11 | No persistent schema change. APPROVED subject to the check in §37.2. |
| D12 | `Goal.success_criteria` remains free-form context. APPROVED. |

### 37.2 Persistence clearance (pre-code)

- `TaskNode`, `Plan`, `Goal` are not serialised by any code in `src/`. Verified by grep at HEAD `becea8a`.
- Durable AGI state is `goals.json` (`GoalRecord`), `reflections.json` (`ReflectionRecord`) and tool metrics. None contains a task or plan.
- Telemetry `plan_generated` carries only `task_count`.
- **Result: no `schema_version: 1` structure is touched. No migration. Clearance granted.**

### 37.3 Implementation contract (for the tests-first step)

- New module: `src/warnetech_core/agi/verification.py` with `VerificationCheckKind` (enum: `EXECUTION_SUCCESS`, `RESULT_PRESENT`), `VerificationCheck` (frozen, `kind` only, `extra="forbid"`), `TaskVerificationContract` (frozen, `checks` tuple), and `MAX_VERIFICATION_CHECKS = 2`.
- `TaskNode` gains `verification: TaskVerificationContract | None = None`.
- Parser: absent or `null` → `None`. Explicit `{"checks": []}` → `None`. Any malformed explicit contract → `ValueError`. Order of checks: count, then unknown key/kind, then duplicates.
- Orchestrator: wrap only the `await self.planner.decompose(goal)` call in `except ValueError`, record `failed` through the existing `_persist` path, then `raise`.
- Unchanged: candidates, ranking, retry, failover, reflection, `OutcomeAnalysis`, telemetry event set, persistence schema.

### 37.4 RED test file

`tests/test_agi_task_verification_contract.py`, CONTRACT-001 to CONTRACT-016 as listed in the approval. Current state: collection error (`ModuleNotRegistered` import of `warnetech_core.agi.verification`), which is the expected RED state before implementation.

### 37.5 Known consequence

A malformed contract now aborts the whole goal, not one task. This is the approved behaviour (D10). Valid plans are unaffected.
