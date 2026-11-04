import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("RAGStore")

class RAGStore:
    def __init__(self, storage_dir: str = "./data/rag_memory") -> None:
        self.storage_dir = Path(storage_dir)
        self.index_file = self.storage_dir / "index.jsonl"
        self._cache: list[dict[str, Any]] = []

    async def initialize(self) -> None:
        """Ensure storage paths exist and load memory index into memory cache."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_file.exists():
            self.index_file.touch()
        await self._load_cache()
        logger.info(
            f"RAGStore initialized with {len(self._cache)} items loaded from {self.storage_dir}"
        )

    async def _load_cache(self) -> None:
        """Reads flat-file index storage asynchronously."""
        self._cache.clear()
        if not self.index_file.exists():
            return
        
        loop = asyncio.get_running_loop()
        def _read_file():
            items = []
            with open(self.index_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            items.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            return items

        self._cache = await loop.run_in_executor(None, _read_file)

    async def add_document(
        self, doc_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Persists a new document fragment to the flat-file store."""
        entry = {
            "id": doc_id,
            "content": content,
            "metadata": metadata or {}
        }
        
        loop = asyncio.get_running_loop()
        def _append_file():
            with open(self.index_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

        await loop.run_in_executor(None, _append_file)
        self._cache.append(entry)
        logger.debug(f"Added document ID {doc_id} to RAG store.")

    async def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Performs basic keyword relevance retrieval over flat-file items."""
        if not self._cache:
            return []

        query_tokens = set(query.lower().split())
        scored_results = []

        for item in self._cache:
            content_lower = item["content"].lower()
            # Calculate simple token overlap score
            score = sum(1 for token in query_tokens if token in content_lower)
            if score > 0:
                scored_results.append((score, item))

        # Sort by score descending
        scored_results.sort(key=lambda x: x[0], reverse=True)
        
        return [item for _, item in scored_results[:top_k]]

    async def close(self) -> None:
        """Graceful cleanup for store resources."""
        logger.info("Closing RAGStore resources.")
        self._cache.clear()

if __name__ == "__main__":
    async def test():
        store = RAGStore("./test_rag_data")
        await store.initialize()
        await store.add_document(
            "doc_1",
            "The temporal engine coordinates state transitions across distributed nodes.",
        )
        results = await store.retrieve("temporal engine")
        print("Retrieval test results:", results)
        
        # Cleanup test files
        import shutil
        shutil.rmtree("./test_rag_data", ignore_errors=True)

    asyncio.run(test())
