"""Static registry of AGI components and their metadata (Phase 1, D1).

Holds metadata only: name, description, version and declared dependencies.
Components are registered in code at startup, not discovered from the filesystem.
Tools with handlers stay in :class:`~warnetech_core.agi.tools.ToolRegistry`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Capability(BaseModel):
    """Metadata for one AGI component."""

    name: str
    description: str = ""
    version: str = "1.0.0"
    dependencies: list[str] = Field(default_factory=list)


class CapabilityRegistry:
    """Single source of truth for registered capability metadata."""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability, *, replace: bool = False) -> None:
        """Add *capability*. A duplicate name raises unless *replace* is true."""
        if capability.name in self._capabilities and not replace:
            raise ValueError(f"capability already registered: {capability.name}")
        self._capabilities[capability.name] = capability

    def get_capability(self, name: str) -> Capability | None:
        """Return the capability named *name*, or None when unknown."""
        return self._capabilities.get(name)

    def list_capabilities(self) -> list[Capability]:
        """All registered capabilities, sorted by name."""
        return sorted(self._capabilities.values(), key=lambda cap: cap.name)

    def exists(self, name: str) -> bool:
        return name in self._capabilities

    def validate_dependencies(self) -> list[str]:
        """Report dependencies on unknown names and dependency cycles. Empty means valid."""
        problems: list[str] = []
        for cap in self._capabilities.values():
            for dep in cap.dependencies:
                if dep not in self._capabilities:
                    problems.append(f"{cap.name} depends on unknown capability {dep}")
        problems.extend(self._find_cycles())
        return problems

    def _find_cycles(self) -> list[str]:
        problems: list[str] = []
        finished: set[str] = set()

        def visit(name: str, path: list[str]) -> None:
            if name in path:
                cycle = path[path.index(name) :] + [name]
                problems.append("dependency cycle: " + " -> ".join(cycle))
                return
            if name in finished or name not in self._capabilities:
                return
            for dep in self._capabilities[name].dependencies:
                visit(dep, [*path, name])
            finished.add(name)

        for name in self._capabilities:
            visit(name, [])
        return problems


def register_builtin_capabilities(registry: CapabilityRegistry) -> None:
    """Register the Phase 1 AGI components with their real dependencies."""
    registry.register(Capability(name="planner", description="Decomposes a goal into a task graph"))
    registry.register(
        Capability(name="reasoner", description="Structured chain-of-thought reasoning")
    )
    registry.register(
        Capability(name="reflector", description="Evaluates outputs and flags retries")
    )
    registry.register(
        Capability(
            name="orchestrator",
            description="Runs plan, reason, act, reflect over a goal",
            dependencies=["planner", "reasoner", "reflector"],
        )
    )
