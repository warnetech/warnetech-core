"""Candidate resolution for tool selection (selection-group contract).

Only explicit ``ToolDefinition.selection_group`` membership makes tools
alternatives. Tags, names and descriptions are never used. This module reads
tool metadata only and never reads ToolMetrics.
"""

from __future__ import annotations

from .tools import ToolDefinition, ToolRegistry


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
