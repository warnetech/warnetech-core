import asyncio
import logging
import aiohttp
from typing import Dict, Any, Optional

logger = logging.getLogger("ProviderRouter")

class ProviderRouter:
    def __init__(self, default_provider: str = "local", local_endpoint: str = "http://127.0.0.1:8080/v1") -> None:
        self.default_provider = default_provider
        self.local_endpoint = local_endpoint
        self._session: Optional[aiohttp.ClientSession] = None

    async def initialize(self) -> None:
        """Initialize HTTP session for remote/local model requests."""
        if not self._session:
            self._session = aiohttp.ClientSession()
        logger.info(f"ProviderRouter initialized with default provider: {self.default_provider}")

    async def generate(self, prompt: str, system_prompt: Optional[str] = None, max_tokens: int = 512, temperature: float = 0.7) -> str:
        """Route completion request to the active LLM backend."""
        if self.default_provider == "local":
            return await self._call_local_llama(prompt, system_prompt, max_tokens, temperature)
        else:
            raise NotImplementedError(f"Provider '{self.default_provider}' is not configured.")

    async def _call_local_llama(self, prompt: str, system_prompt: Optional[str], max_tokens: int, temperature: float) -> str:
        if not self._session:
            await self.initialize()

        payload = {
            "prompt": f"System: {system_prompt}\nUser: {prompt}\nAssistant:" if system_prompt else f"User: {prompt}\nAssistant:",
            "n_predict": max_tokens,
            "temperature": temperature,
            "cache_prompt": True
        }

        try:
            async with self._session.post(f"{self.local_endpoint}/completion", json=payload) as response:
                if response.status != 200:
                    text_err = await response.text()
                    raise RuntimeError(f"Local LLM engine returned status {response.status}: {text_err}")
                
                data = await response.json()
                return data.get("content", "").strip()
        except Exception as e:
            logger.error(f"Failed to communicate with local LLM endpoint: {e}", exc_info=True)
            raise

    async def close(self) -> None:
        """Close active network sessions."""
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("ProviderRouter session closed.")

if __name__ == "__main__":
    async def test():
        router = ProviderRouter()
        await router.initialize()
        # Mock test run structure
        await router.close()

    asyncio.run(test())
