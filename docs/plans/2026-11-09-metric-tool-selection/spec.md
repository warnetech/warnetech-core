---
status: implemented / closed
baseline: bf904c2
---

# Metric-informed tool selection (amended)

## Current runtime

- A task names at most one tool in `Task.tool_hint` (`planning.py`). The planner describes it as "a tool that could help".
- `AGIOrchestrator` resolves that name and executes it (`orchestrator.py`, around line 166). No candidate set is built.
- `ToolMetrics` records outcomes and ranks tools. Selection ignores it.

## Decisions

1. `tool_hint` is advisory. It seeds candidate discovery. When the feature is enabled, a better-ranked compatible tool may replace it.
2. The feature is off by default. With it off, selection is unchanged from `bf904c2`.
3. Candidate discovery runs before ranking. Ranking never adds a tool to the set.
4. An unknown `tool_hint` keeps the existing failure. Ranking never falls back to the whole registry.
5. A hinted tool with no compatible alternatives executes as it does today.
6. Candidate discovery reads tool metadata only. It never reads metrics.
7. Ranking and selection are read-only with respect to metrics.
8. Corrupt or unsupported metrics raise `StateError`, as in P2-4.
9. No new persistence schema. No automatic retry. No random exploration.

## Blocker: `ToolDefinition.tags` are not safe for substitution

Candidate discovery as first proposed would take "shares at least one tag with the hinted tool". Production tags do not support that rule:

- `src/warnetech_core/interpreter/agent_tools.py` tags `workspace_read`, `workspace_write` and `workspace_list` with `["interpreter", "files"]`.
- The same file tags `run_code` with `["interpreter", "exec"]`.

Under the shared-tag rule, `run_code` (arbitrary code execution) would become a candidate for a hint of `workspace_read`. A high metric ranking would then let it execute. The rule is unsafe.

Tags are descriptive labels. They are not interchangeability contracts. Only `list_tools(tags=...)` filters on them.

**Gate:** tag-based candidate discovery stays blocked.

## Proposed alternative (needs approval)

Add an explicit optional field to `ToolDefinition`:

```python
selection_group: str | None = None
```

- Tools with the same non-empty `selection_group` are interchangeable alternatives. The owner of the tool asserts this.
- A tool with `selection_group=None` has no alternatives. Its hint executes as today.
- Candidate set = the hinted tool plus every registered tool in the same group.
- Tags stay descriptive and are not used for substitution.

Example: `workspace_read`, `workspace_write` and `workspace_list` are not substitutes for one another. Each needs its own group, or `None`. Only tools that really do the same job share a group.

This needs its own approval, because it adds a field to the tool model.

## Acceptance criteria (before implementation)

- MTS-AC1 Default off: with the feature disabled, selection matches `bf904c2` for existing constructor calls.
- MTS-AC2 Enabled: a compatible alternative with a higher `ToolMetrics` ranking executes in place of the hint.
- MTS-AC3 Group boundary: a tool outside the hinted tool's group never executes, even with the highest ranking.
- MTS-AC4 Hint is advisory when enabled, and fixed when disabled.
- MTS-AC5 No group: the hint executes.
- MTS-AC6 Unknown hint: existing failure, no registry-wide fallback.
- MTS-AC7 Cold start: an untried candidate is ranked by the existing `ToolMetrics` prior. Its absence of history is not an error.
- MTS-AC8 Ties: equal ranks resolve in candidate order, with the hint first.
- MTS-AC9 Corrupt or unsupported metrics raise `StateError`.
- MTS-AC10 Selection and discovery do not change metrics.
- MTS-AC11 Restart with the same metrics and registry selects the same tool.
- MTS-AC12 `tool_selected` reports the winner and the selection mode (`default` or `metrics`).
- MTS-AC13 Ranking does not expand the candidate set.
- MTS-AC14 Tag-based selection is absent: a shared tag does not make a tool a candidate.

## Sabotage checks (after implementation)

- Ignore the group boundary: MTS-AC3 must go red.
- Force metrics on: MTS-AC1 must go red.
- Use tags as the group: MTS-AC14 must go red.
- Skip version validation: MTS-AC9 must go red.

## Status

- Candidate layer: **implemented** in `aa453fd` (`ToolDefinition.selection_group` default `None`, `agi/candidates.py:resolve_candidates`). Tests in `tests/test_agi_candidates.py`. Tag-based discovery is rejected.
- Ranking wired into runtime selection: **implemented, opt-in** (`enable_metric_selection=True`, which requires `enable_phase2=True` and `state_dir`). Tests: `tests/test_agi_metric_selection.py`. Off by default, so runtime selection is unchanged.
- Ranking picks among `resolve_candidates()` output with `pick_candidate()`. Ties keep candidate order, so the hint wins.
- Outcomes are recorded for the tool that actually ran, not the hint.
- `tool_selected` adds `selection_mode` only when the flag is on.
- Candidate sabotage checks (each restored, not committed): tag matching -> 2 red; None matches None -> 3 red; ignore explicit group -> 2 red; drop hinted tool -> 4 red. Gate: candidate review closed pending final ranking spec review.
- Candidate review gate: real `workspace_read`/`run_code` regression added (`test_real_workspace_read_does_not_pick_up_run_code`). Sabotage to tag matching turns both shared-tag regressions red. Pending: review of real non-null groups (none assigned yet).
- Groups are not assigned to any production tool yet. Each assignment needs a semantic review.


## Closure record

- Implementation: `fffaf6b`. Candidate layer: `aa453fd`. Real-registration regression: `3efb80e`.
- Validation: 1039 passed, 2 skipped, lint clean.
- Default off. Requires `enable_phase2=True` and `state_dir`.
- Only explicit `selection_group` candidates participate. Tags never do.
- Metrics are attributed to the tool that actually executed. `tool_selected` reports the winner, and adds `selection_mode` only when the flag is on.
- No production selection groups exist, so no production tool substitution occurs. Assigning groups is a separate decision requiring semantic review.
- Not implemented, by design: automatic retry, automatic failover, reflection-based ranking, random exploration, production group auto-assignment.
