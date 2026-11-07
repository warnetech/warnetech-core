import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("AgentOrchestrator")

AgentHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

class AgentOrchestrator:
    def __init__(self) -> None:
        self.agents: dict[str, AgentHandler] = {}
        self.execution_pipeline: list[str] = []

    def register_agent(self, name: str, handler: AgentHandler) -> None:
        """Register an agent sub-routine handler."""
        self.agents[name] = handler
        if name not in self.execution_pipeline:
            self.execution_pipeline.append(name)
        logger.info(f"Registered agent '{name}' into orchestrator pipeline.")

    def set_pipeline(self, pipeline: list[str]) -> None:
        """Define explicit ordered execution pipeline of agent names."""
        for name in pipeline:
            if name not in self.agents:
                raise ValueError(f"Agent '{name}' must be registered before adding to pipeline.")
        self.execution_pipeline = pipeline
        logger.info(f"Updated orchestration execution pipeline: {self.execution_pipeline}")

    async def execute(self, initial_payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the registered agent pipeline sequentially with shared state context."""
        state = initial_payload.copy()
        logger.info(f"Starting orchestration execution over {len(self.execution_pipeline)} agents.")

        for agent_name in self.execution_pipeline:
            handler = self.agents.get(agent_name)
            if not handler:
                logger.warning(f"Skipping missing agent handler: {agent_name}")
                continue

            try:
                logger.debug(f"Executing agent: {agent_name}")
                state = await handler(state)
                state["_last_executed_agent"] = agent_name
            except Exception as e:
                logger.error(f"Critical error in agent '{agent_name}': {e}", exc_info=True)
                state["error"] = str(e)
                state["failed_agent"] = agent_name
                break

        logger.info("Orchestration execution sequence finished.")
        return state

if __name__ == "__main__":
    async def sample_agent_alpha(state: dict[str, Any]) -> dict[str, Any]:
        state["alpha_processed"] = True
        state["counter"] = state.get("counter", 0) + 1
        return state

    async def sample_agent_beta(state: dict[str, Any]) -> dict[str, Any]:
        state["beta_processed"] = True
        state["counter"] = state.get("counter", 0) + 1
        return state

    async def test():
        orchestrator = AgentOrchestrator()
        orchestrator.register_agent("alpha", sample_agent_alpha)
        orchestrator.register_agent("beta", sample_agent_beta)
        
        result = await orchestrator.execute({"task": "test_pipeline"})
        print("Orchestrator execution result:", result)

    asyncio.run(test())
