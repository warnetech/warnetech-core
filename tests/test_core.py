import asyncio
import pytest
import shutil
from pathlib import Path

from core.temporal_engine import TemporalEngine
from memory.rag_store import RAGStore
from agents.orchestrator import AgentOrchestrator

@pytest.mark.asyncio
async def test_temporal_engine():
    engine = TemporalEngine()
    
    async def hook_one(state):
        state["val"] = state.get("val", 0) + 1
        return state

    engine.register_hook(hook_one)
    res = await engine.step({"val": 10})
    assert res["val"] == 11

@pytest.mark.asyncio
async def test_rag_store():
    test_dir = "./test_pytest_rag"
    store = RAGStore(storage_dir=test_dir)
    await store.initialize()
    
    await store.add_document("doc_1", "Artificial General Intelligence temporal architectures.")
    results = await store.retrieve("temporal architectures")
    
    assert len(results) == 1
    assert results[0]["id"] == "doc_1"
    
    await store.close()
    shutil.rmtree(test_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_agent_orchestrator():
    orchestrator = AgentOrchestrator()

    async def agent_a(state):
        state["a"] = True
        return state

    async def agent_b(state):
        state["b"] = True
        return state

    orchestrator.register_agent("agent_a", agent_a)
    orchestrator.register_agent("agent_b", agent_b)
    orchestrator.set_pipeline(["agent_a", "agent_b"])

    res = await orchestrator.execute({"initial": 100})
    assert res["a"] is True
    assert res["b"] is True
    assert res["_last_executed_agent"] == "agent_b"
