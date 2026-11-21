---
status: active governance framework
baseline: b261f26
production_selection_groups: none
---

# Production selection-group review framework (PB-2)

## Purpose

A production `selection_group` lets the selection system consider one tool as a substitute for another. That is runtime authority, so it needs review before it is introduced. This framework defines that review. It does not assign any group.

Scope: documentation and governance only. Changing the mechanism, ranking, telemetry or persistence needs a separate specification.

## Rules that do not change

- Default: `selection_group = None`. It means "no approved substitution", never "shared default group".
- Candidate membership is decided by `resolve_candidates()` from explicit `selection_group` values, before ranking.
- Tags, names, descriptions, module paths, handler families, LLM judgement and ToolMetrics history never create membership. Shared tags do not imply interchangeability.
- Ranking chooses only among approved candidates. Ties keep the hinted tool.
- Outcomes are recorded for the tool that executed.
- Metric selection stays off by default.
- Persistent state (`schema_version: 1`) is unchanged.

## Lifecycle

```text
PROPOSED -> UNDER REVIEW -> APPROVED -> TESTED -> ACTIVATED
                         \-> REJECTED
ACTIVATED -> RETIRED
```

- **Approved** means the semantic review passed. It does not change production code.
- **Tested** means the required tests in this document pass against real registrations.
- **Activated** means the `selection_group` assignment is committed.
- A proposal may stay at APPROVED for a long time before activation.

A proposal is never APPROVED because its tools pass unit tests. Semantic review is required.

## Review identity

- Review ID: `SGR-NNN`, numbered in order. Use this instead of another numbering scheme.
- Group name: stable and semantic. It names the interchangeable operation (for example `read_text_file`), not a module, a team, or a ranking result. Group names are opaque to the runtime and match exactly.

## Required sections

Every proposal fills in all of these. A section marked UNCERTAIN blocks approval.

1. **Intended operation.** The behavioural description of the shared operation. Vague categories ("utilities", "interpreter tools") are not enough.
2. **Tool inventory.** Explicit list of tools. Open-ended membership ("all tools with tag X") is rejected.
3. **Semantic compatibility.** PASS / FAIL / UNCERTAIN, with evidence.
4. **Input compatibility.** Required, optional and context inputs per member. Can every member run from the same task-level information? If one member needs different information, the group is BLOCKED unless an adaptation layer already exists. This framework does not approve building one.
5. **Output compatibility.** Result type, semantics, error representation. Can the downstream path accept any member's result without branching on which member ran?
6. **Side-effect compatibility.** Classify each member: read-only, filesystem mutation, network mutation, process execution, code execution, configuration mutation, external service mutation, security-sensitive. Materially different classes should not share a group.
7. **Privilege compatibility.** Does any member have broader authority? Could substitution widen the operation beyond what the hint implied? If yes, the proposal is REJECTED unless a separate specification authorises that transition.
8. **Failure compatibility.** Per member: exception types, partial results, side effects before failure, cleanup. Does substitution change failure semantics enough to make the group unsafe?
9. **Idempotency.** Idempotent, non-idempotent, or unknown, per member, and the effect on interchangeability.
10. **Planner intent.** Would substituting any member still satisfy the operation the planner hinted? YES / NO / UNCERTAIN. UNCERTAIN blocks approval.
11. **Candidate-set consequence.** Before and after `resolve_candidates(<member>)` for every member.
12. **Metric-selection consequence.** Once active with metric selection on, any member may execute if it ranks higher. Reviewers must not assume the hint always wins.
13. **Tie consequence.** Ties keep the hint. If this group needs a different tie rule, stop and write a separate selection-policy specification.
14. **Telemetry expectation.** Expected `hinted_tool`, `candidate_tools`, `selected_tool`, `selection_mode`, `selection_reason` for a representative case. Uses the existing `tool_selected` event only.
15. **Historical readiness (informational).** Which members have ToolMetrics history, and whether cold start could produce an unexpected winner. Do not reset or reshape metrics to make activation easier. Do not change the ranking algorithm inside a group commit.
16. **Required tests.** Listed before activation (see below).
17. **Rollback.** How to remove the assignment. Normally a code change that sets `selection_group = None`. State explicitly whether any persistent-state, metric or telemetry change is needed. Metrics are not reset by default.
18. **Decision.** APPROVED / REJECTED / BLOCKED, with reason.

## Template

```markdown
# SGR-NNN: <group name>

Status: PROPOSED | UNDER REVIEW | APPROVED | REJECTED | RETIRED
Date:
Baseline:

## Tools
- ...

## Intended operation

## Semantic compatibility      PASS | FAIL | UNCERTAIN
Evidence:

## Input compatibility         PASS | FAIL | UNCERTAIN
## Output compatibility        PASS | FAIL | UNCERTAIN
## Side-effect compatibility   PASS | FAIL | UNCERTAIN
## Privilege compatibility     PASS | FAIL | UNCERTAIN   (any member broader? YES/NO)
## Failure compatibility       PASS | FAIL | UNCERTAIN
## Idempotency
## Planner intent              YES | NO | UNCERTAIN

## Candidate-set consequence
Before: resolve_candidates(...) -> [...]
After:  resolve_candidates(...) -> [...]

## Metric-selection consequence

## Tie consequence

## Telemetry expectation
hinted_tool:
candidate_tools:
selected_tool:
selection_mode:
selection_reason:

## Historical readiness (informational)

## Required tests
- [ ] ...

## Rollback

## Decision: APPROVED | REJECTED | BLOCKED
Reason:
```

## Required tests before activation

Use real production registrations where practical. Synthetic fixtures are acceptable for the mechanism, but not as the only evidence for a real group.

- Group membership: `resolve_candidates(member)` returns exactly the approved members.
- Outside-group exclusion: a real tool outside the group, with stronger ToolMetrics history, never executes.
- Shared-tag irrelevance: tools sharing a tag but not a group stay isolated.
- Hint preservation: the hinted tool stays in its own candidate set.
- Feature-off compatibility: with metric selection off, the hinted tool executes as before.
- Feature-on winner: with `enable_phase2=True`, `state_dir` set and `enable_metric_selection=True`, an approved higher-ranked member executes.
- Tie preserves hint: equal ranking keeps the hint.
- Telemetry: `tool_selected` shows the hint, the resolver-order candidates, the actual winner, the mode and the reason.
- Executed-tool attribution: outcome recorded against the member that ran.

## Sabotage checks for activation

Run locally. Restore after each. Do not commit.

- G1: remove one member from the group. Expected: membership test RED.
- G2: add an unrelated tool to the group. Expected: outside-group test RED.
- G3: force the hint to win despite a higher approved ranking. Expected: feature-on winner test RED.
- G4: report the hint instead of the executed winner in telemetry. Expected: telemetry test RED.

## Activation workflow

1. Create the SGR record. List the exact tools.
2. Complete every required section. Any UNCERTAIN blocks.
3. Record APPROVED in the SGR record.
4. Write the real-registration tests. Confirm they are RED before assignment.
5. Assign `selection_group`.
6. Confirm the tests are GREEN.
7. Run sabotage G1 to G4. Restore.
8. Run the full suite and lint.
9. Mark TESTED, then ACTIVATED. Commit the assignment separately from the SGR record.
10. Record the new baseline.

No group skips this workflow.

## Automatic grouping is prohibited

The following must never create or extend a group: tags, names, descriptions, vector or text similarity, LLM judgement, shared module or handler, historical success or ranking. Membership is always declared by a person in an SGR record.

## Governance self-check

The framework is inadequate if any answer below is YES.

- Can a group be approved without semantic compatibility? NO: the Semantic section must read PASS.
- Can a group be approved without privilege review? NO: the Privilege section must be filled in.
- Can the candidate-set change be hidden? NO: the candidate-set section is required.
- Can production groups be activated automatically? NO: activation is a committed assignment after the workflow.
- Can this framework change ranking policy? NO: it defines no ranking behaviour.

## Current state

- Production non-null `selection_group` assignments: **none** (`b261f26`).
- SGR records approved: **none**.
- Runtime, ToolMetrics, candidate resolver, telemetry and persistence: unchanged by this framework.

## Next slice

PB-3, Execution Identity Consistency Audit. It checks code that might assume `tool_hint == executed_tool`. It is not started by this framework and needs a separate instruction.
