"""AGI capabilities for warnetech-core.

Composable building blocks that layer on top of the AI gateway and memory
service to produce autonomous, goal-directed behaviour:

- **Reasoning** — chain-of-thought and tree-of-thought structured thinking
- **Planning** — goal decomposition into executable task graphs
- **Tool use** — MCP-based tool discovery, validation and execution
- **Reflection** — self-evaluation, error detection, output correction
- **Orchestrator** — the AGI loop tying everything together

Each module works independently or composes through the orchestrator.
All model calls go through :class:`~warnetech_core.gateway.AIGateway`.
"""

from .orchestrator import AGIOrchestrator
from .planning import Goal, Plan, Planner, PlanStatus, TaskNode
from .reasoning import ReasoningEngine, ReasoningStrategy, ThoughtStep
from .reflection import Critique, CritiqueSeverity, Reflector
from .tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult

__all__ = [
    "AGIOrchestrator",
    "Critique",
    "CritiqueSeverity",
    "Goal",
    "Plan",
    "PlanStatus",
    "Planner",
    "ReasoningEngine",
    "ReasoningStrategy",
    "Reflector",
    "TaskNode",
    "ThoughtStep",
    "ToolCall",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
]
