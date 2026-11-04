import asyncio
import logging
import signal
from typing import Any

from warnetech_mcp.stdio_protocol import MCPServer

from warnetech_core.agi.context_window import ContextWindow
from warnetech_core.agi.temporal_engine import TemporalEngine
from warnetech_core.memory.rag_store import RAGStore
from warnetech_core.providers.provider_router import ProviderRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("WarnetechCore")

class ApplicationHost:
    def __init__(self) -> None:
        self.engine = TemporalEngine()
        self.rag = RAGStore()
        self.mcp = MCPServer()
        self.router = ProviderRouter(default_provider="local")
        self.context_window = ContextWindow(max_tokens=4096)
        self._shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """Bootstrap system components, LLM router, and execution hooks."""
        logger.info("Initializing RAG storage backend...")
        await self.rag.initialize()
        
        logger.info("Initializing LLM Provider Router...")
        await self.router.initialize()

        logger.info("Binding RAG and LLM inference middleware to TemporalEngine...")
        self.engine.register_hook(self._llm_reasoning_hook)
        
        logger.info("Starting Model Context Protocol (MCP) server bridge...")
        await self.mcp.start()
        
        logger.info("Warnetech Core full pipeline initialization complete.")

    async def _llm_reasoning_hook(self, state: dict[str, Any]) -> dict[str, Any]:
        """Retrieves RAG context, assembles prompt window, and executes LLM completion."""
        query = state.get("active_query")
        if not query:
            return state

        logger.debug(f"Executing RAG retrieval for query: {query}")
        rag_chunks = await self.rag.retrieve(query)
        
        system_prompt = state.get("system_prompt", "You are an advanced autonomous AGI core engine.")
        chat_history = state.get("chat_history", [])

        assembled_prompt = self.context_window.assemble_prompt(
            system_prompt=system_prompt,
            rag_chunks=rag_chunks,
            chat_history=chat_history,
            current_query=query
        )

        logger.debug("Executing local LLM generation...")
        response_text = await self.router.generate(
            prompt=assembled_prompt,
            system_prompt=system_prompt,
            max_tokens=512,
            temperature=0.7
        )

        state["llm_response"] = response_text
        state["retrieved_context"] = rag_chunks
        return state

    async def run(self) -> None:
        """Execute the primary runtime loop."""
        await self.initialize()
        
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break

        logger.info("Executing graceful system shutdown...")
        await self.mcp.stop()
        await self.router.close()
        await self.rag.close()
        logger.info("Warnetech Core shutdown complete.")

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

if __name__ == "__main__":
    host = ApplicationHost()
    
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, host.request_shutdown)

    try:
        loop.run_until_complete(host.run())
    except Exception as e:
        logger.critical(f"Unhandled fatal exception during execution: {e}", exc_info=True)
        raise
    finally:
        loop.close()
