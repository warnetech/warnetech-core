"""Alignment with the existing Warnetech system (claude-command-cli).

These cover the pieces added specifically to fit warnetech_core into the
canonical architecture: the NVIDIA/Nemotron provider the firewall already
uses, the shared-Supabase store, and the embedding-model isolation that a
shared vector table needs.
"""

import httpx
import pytest

from warnetech_core import AIGateway
from warnetech_core.errors import ConfigError
from warnetech_core.memory.inmemory import InMemoryContextStore
from warnetech_core.memory.service import MemoryService
from warnetech_core.memory.sqlite import SQLiteContextStore
from warnetech_core.providers.nvidia import NvidiaProvider
from warnetech_core.registry import ModelRegistry
from warnetech_core.types import CompletionRequest, Message

# ---- NVIDIA / Nemotron --------------------------------------------------

def test_nemotron_is_in_the_catalogue():
    spec = ModelRegistry.default().resolve("chat.nemotron")
    assert spec.provider == "nvidia"
    assert spec.provider_model == "nvidia/nemotron-3-ultra"


def test_nemotron_falls_back_to_general_chat_models():
    chain = ModelRegistry.default().chain("chat.nemotron")
    assert chain[0].provider == "nvidia"
    assert {s.provider for s in chain[1:]} == {"anthropic", "openai"}


def test_offline_mode_covers_the_nemotron_alias():
    # A task alias that escaped offline mapping would make an offline run
    # attempt a real network call.
    registry = ModelRegistry.default().use_offline_models()
    assert registry.resolve("chat.nemotron").provider == "echo"


def test_nvidia_is_a_separate_provider_from_openai():
    # Same wire format, but cost and rate limits must not share a bucket.
    assert NvidiaProvider(api_key="k").name == "nvidia"


def test_nvidia_requires_a_key():
    import os

    saved = os.environ.pop("NVIDIA_API_KEY", None)
    try:
        with pytest.raises(ConfigError, match="NVIDIA_API_KEY"):
            NvidiaProvider()
    finally:
        if saved is not None:
            os.environ["NVIDIA_API_KEY"] = saved


async def test_nvidia_speaks_the_openai_wire_format():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "c1",
            "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://nv")
    provider = NvidiaProvider(client=client)
    spec = ModelRegistry.default().resolve("chat.nemotron")
    response = await provider.complete(
        CompletionRequest(model="chat.nemotron", messages=[Message.user("ping")]), spec
    )

    assert seen["path"] == "/chat/completions"
    assert seen["body"]["model"] == "nvidia/nemotron-3-ultra"
    assert response.text == "pong"
    assert response.provider == "nvidia"


# ---- Supabase store -----------------------------------------------------

def test_supabase_store_reports_a_missing_connection_string():
    import os

    from warnetech_core.memory.supabase import SupabaseVectorStore

    saved = {k: os.environ.pop(k, None)
             for k in ("WARNETECH_SUPABASE_DSN", "SUPABASE_DB_URL", "DATABASE_URL")}
    try:
        with pytest.raises(ConfigError, match="WARNETECH_SUPABASE_DSN"):
            SupabaseVectorStore()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_supabase_store_defaults_to_never_issuing_ddl():
    # The shared project holds other components' live data; silent CREATE TABLE
    # against it is exactly what supabase_schema/migrations.py guards against.
    pytest.importorskip("psycopg_pool", reason="needs the `pgvector` extra")
    from warnetech_core.memory.supabase import SupabaseVectorStore

    store = SupabaseVectorStore("postgresql://u:p@localhost:5432/db")
    assert store.manage_schema is False


def test_pgvector_store_manages_schema_by_default():
    # A database you own outright is fine to migrate automatically; only the
    # shared Supabase project opts out.
    pytest.importorskip("psycopg_pool", reason="needs the `pgvector` extra")
    from warnetech_core.memory.pgvector import PgVectorContextStore

    assert PgVectorContextStore("postgresql://u:p@localhost:5432/db").manage_schema is True


def test_migration_is_additive_only():
    """No ALTER/DROP/REPLACE may reach the shared Supabase project."""
    from pathlib import Path

    # Resolve from this file, not the working directory, so the test holds
    # wherever pytest is invoked from.
    root = Path(__file__).resolve().parent.parent
    sql = (root / "migrations" / "0001_warnetech_memory.sql").read_text().lower()
    statements = [
        line for line in sql.splitlines() if line.strip() and not line.strip().startswith("--")
    ]
    body = "\n".join(statements)
    assert "drop " not in body
    assert "alter table" in body  # only the RLS enable
    assert body.count("alter table") == 1 and "enable row level security" in body
    assert "or replace" not in body
    # Every creating statement must be idempotent.
    assert body.count("create table") == body.count("create table if not exists")
    assert body.count("create extension") == body.count("create extension if not exists")
    assert body.count("create index") == body.count("create index if not exists")
    assert body.count("create unique index") == body.count("create unique index if not exists")


# ---- embedding-model isolation -----------------------------------------

@pytest.fixture(params=["memory", "sqlite"])
def isolation_store(request):
    return InMemoryContextStore() if request.param == "memory" else SQLiteContextStore(":memory:")


async def test_records_carry_the_model_that_embedded_them(isolation_store):
    memory = MemoryService(AIGateway.offline(), isolation_store)
    record = await memory.remember("p", "a fact", key="k")
    assert record.embed_model == "embed.default"


async def test_unembedded_records_carry_no_model(isolation_store):
    memory = MemoryService(AIGateway.offline(), isolation_store)
    record = await memory.remember("p", "a fact", key="k", embed=False)
    assert record.embed_model is None


async def test_a_model_swap_hides_stale_vectors_instead_of_misscoring_them(isolation_store):
    # Two models can share a dimensionality while producing incomparable
    # vectors; scoring across them yields confident nonsense.
    gateway = AIGateway.offline()
    written = MemoryService(gateway, isolation_store, embed_model="embed.default")
    await written.remember("p", "a fact worth finding", key="k")
    assert len(await written.recall("p", "a fact worth finding")) == 1

    swapped = MemoryService(gateway, isolation_store, embed_model="embed.large")
    assert await swapped.recall("p", "a fact worth finding") == []
