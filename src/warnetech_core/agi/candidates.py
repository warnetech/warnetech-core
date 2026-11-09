"""Candidate resolution for tool selection (selection-group contract).

Only explicit ``ToolDefinition.selection_group`` membership makes tools
alternatives. Tags, names and descriptions are never used. This module reads
tool metadata only and never reads ToolMetrics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .tools import ToolDefinition, ToolRegistry

if TYPE_CHECKING:
    from .tool_metrics import ToolMetrics


def resolve_candidates(tool_hint: str, registry: ToolRegistry) -> list[ToolDefinition]:
    """Return the tools that may execute in place of *tool_hint*.

    - Unknown or empty hint: empty list. The caller keeps its existing behaviour.
    - Hinted tool with ``selection_group=None``: just the hinted tool.
    - Otherwise: the hinted tool first, then every other registered tool with
      exactly the same non-null group, in registry order.
    """
    if not tool_hint:
        return []
    hinted = registry.get(tool_hint)
    if hinted is None:
        return []
    if hinted.selection_group is None:
        return [hinted]
    alternatives = [
        tool
        for tool in registry.list_tools()
        if tool.name != hinted.name and tool.selection_group == hinted.selection_group
    ]
    return [hinted, *alternatives]


def pick_candidate(candidates: list[ToolDefinition], metrics: ToolMetrics) -> ToolDefinition:
    """Return the candidate with the highest posterior mean. Ties keep candidate order.

    Ranking only orders candidates that resolve_candidates() already approved.
    It never adds one. Reads metrics, does not write them.
    """
    if not candidates:
        raise ValueError("no candidates to pick from")
    best = candidates[0]
    best_mean = metrics.posterior_mean(best.name)
    for candidate in candidates[1:]:
        mean = metrics.posterior_mean(candidate.name)
        if mean > best_mean:
            best, best_mean = candidate, mean
    return best
