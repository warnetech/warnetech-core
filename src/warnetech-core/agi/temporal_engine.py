import asyncio
import logging
from collections.abc import Callable
from typing import Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TemporalEngine")

class TemporalEngine:
    def __init__(self, state: dict[str, Any] = None):
        self.state = state or {}
        self.registry: list[Callable[..., Any]] = []

    def register_hook(self, func: Callable[..., Any]) -> None:
        self.registry.append(func)

    async def step(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        logger.info(f"Executing temporal step with payload keys: {list(input_payload.keys())}")
        self.state.update(input_payload)
        
        for hook in self.registry:
            if asyncio.iscoroutinefunction(hook):
                self.state = await hook(self.state)
            else:
                self.state = hook(self.state)
                
        return self.state

if __name__ == "__main__":
    async def sample_hook(state: dict[str, Any]) -> dict[str, Any]:
        state["counter"] = state.get("counter", 0) + 1
        return state

    engine = TemporalEngine()
    engine.register_hook(sample_hook)
    
    async def main():
        res = await engine.step({"task": "initialize"})
        print("Engine state output:", res)

    asyncio.run(main())
