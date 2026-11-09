"""Sequential pipeline of named async handlers that share one state dict.

A lightweight alternative to :class:`~warnetech_core.agi.orchestrator.AGIOrchestrator`
for plain function pipelines that call no model. Each handler receives the state
and returns the new state. A handler that raises, or that returns something other
than a dict, stops the run and records the failure in the state.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("warnetech.agi.agent_orchestrator")

AgentHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class AgentOrchestrator:
    """Run registered handlers in order over a shared state dict.

    Example::

        orchestrator = AgentOrchestrator()
        orchestrator.register_agent("fetch", fetch_handler)
        orchestrator.register_agent("summarise", summarise_handler)
        state = await orchestrator.execute({"url": "https://example.com"})
    """

    def __init__(self) -> None:
        self.agents: dict[str, AgentHandler] = {}
        self.execution_pipeline: list[str] = []

    def register_agent(self, name: str, handler: AgentHandler) -> None:
        """Register a handler. A new name joins the end of the pipeline."""
        self.agents[name] = handler
        if name not in self.execution_pipeline:
            self.execution_pipeline.append(name)
        log.info("registered agent %s", name)

    def set_pipeline(self, pipeline: list[str]) -> None:
        """Set the explicit execution order. Every name must already be registered."""
        missing = [name for name in pipeline if name not in self.agents]
        if missing:
            raise ValueError(f"Agent {missing[0]!r} must be registered before adding to pipeline.")
        self.execution_pipeline = list(pipeline)
        log.info("pipeline set to %s", self.execution_pipeline)

    async def execute(self, initial_payload: dict[str, Any]) -> dict[str, Any]:
        """Run the pipeline. The caller's payload is copied, never mutated."""
        state: dict[str, Any] = dict(initial_payload)

        for name in self.execution_pipeline:
            handler = self.agents.get(name)
            if handler is None:
                log.warning("skipping missing agent handler: %s", name)
                continue

            try:
                result = await handler(state)
            except Exception as exc:
                log.error("agent %r failed: %s", name, exc, exc_info=True)
                state["error"] = str(exc)
                state["failed_agent"] = name
                break

            if not isinstance(result, dict):
                message = f"agent {name!r} returned {type(result).__name__}, expected a dict"
                log.error(message)
                state["error"] = message
                state["failed_agent"] = name
                break

            state = result
            state["_last_executed_agent"] = name

        return state
