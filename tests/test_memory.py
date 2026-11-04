import asyncio

import pytest

from warnetech_core.memory.types import Namespace, cosine_similarity


def test_namespace_parses_both_forms():
    assert Namespace.parse("hub").key == "hub:default"
    assert Namespace.parse("hub:user-42").key == "hub:user-42"


@pytest.mark.parametrize("bad", ["", "a:b", "x" * 200, "-leading", "has space"])
def test_namespace_rejects_unsafe_names(bad):
    with pytest.raises(ValueError):
        Namespace(project=bad)


def test_cosine_handles_zero_vectors_instead_of_dividing_by_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


async def test_remember_then_recall_finds_the_record(memory):
    await memory.remember("notes", "We chose Postgres for the billing database.", key="adr-1")
    hits = await memory.recall("notes", "which database for billing")
    assert [h.record.key for h in hits] == ["adr-1"]


async def test_projects_are_isolated_by_default(memory):
    await memory.remember("notes", "Acme invoice is overdue.", key="n")
    await memory.remember("invoices", "Acme invoice is overdue.", key="i")
    hits = await memory.recall("notes", "acme invoice")
    assert {h.record.project for h in hits} == {"notes"}


async def test_cross_project_search_must_be_explicit(memory):
    await memory.remember("notes", "Acme invoice is overdue.", key="n")
    await memory.remember("invoices", "Acme invoice is overdue.", key="i")
    hits = await memory.recall("notes", "acme invoice", also_projects=["invoices"])
    assert {h.record.project for h in hits} == {"notes", "invoices"}


async def test_scopes_partition_within_a_project(memory):
    await memory.remember("hub", "Alice likes tea.", key="pref", scope="user-a")
    await memory.remember("hub", "Bob likes coffee.", key="pref", scope="user-b")
    hits = await memory.recall("hub", "drink preference", scope="user-a")
    assert [h.record.text for h in hits] == ["Alice likes tea."]


async def test_multiple_scopes_can_be_searched_together(memory):
    await memory.remember("hub", "Alice likes tea.", scope="user-a")
    await memory.remember("hub", "Bob likes coffee.", scope="user-b")
    hits = await memory.recall("hub", "drink preference", scopes=["user-a", "user-b"])
    assert len(hits) == 2


async def test_writing_the_same_key_updates_in_place(memory):
    first = await memory.remember("notes", "Standup at 9am.", key="standup")
    second = await memory.remember("notes", "Standup at 10am.", key="standup")
    assert await memory.count("notes") == 1
    assert second.id == first.id            # identity survives the update
    assert second.created_at == first.created_at
    assert (await memory.get("notes", key="standup")).text == "Standup at 10am."


async def test_get_by_key_does_not_call_the_embedder(memory, gateway):
    await memory.remember("notes", "x", key="k")
    before = len(gateway.usage.recent())
    assert (await memory.get("notes", key="k")).text == "x"
    assert len(gateway.usage.recent()) == before


async def test_embedding_can_be_skipped_for_key_only_records(memory, gateway):
    await memory.remember("notes", "opaque blob", key="k", embed=False)
    assert (await memory.get("notes", key="k")).embedding is None
    assert await memory.recall("notes", "opaque blob") == []


async def test_batch_writes_use_a_single_embedding_call(memory, gateway):
    await memory.remember_many("notes", ["one", "two", "three"])
    embed_calls = [r for r in gateway.usage.recent() if r.operation == "embed"]
    assert len(embed_calls) == 1
    assert await memory.count("notes") == 3


async def test_metadata_filter_narrows_results(memory):
    await memory.remember("notes", "shared topic", key="a", metadata={"kind": "decision"})
    await memory.remember("notes", "shared topic", key="b", metadata={"kind": "note"})
    hits = await memory.recall("notes", "shared topic", metadata_filter={"kind": "decision"})
    assert [h.record.key for h in hits] == ["a"]


async def test_metadata_filter_accepts_a_list_as_any_of(memory):
    await memory.remember("notes", "shared topic", key="a", metadata={"kind": "decision"})
    await memory.remember("notes", "shared topic", key="b", metadata={"kind": "note"})
    await memory.remember("notes", "shared topic", key="c", metadata={"kind": "other"})
    hits = await memory.recall(
        "notes", "shared topic", metadata_filter={"kind": ["decision", "note"]}
    )
    assert {h.record.key for h in hits} == {"a", "b"}


async def test_min_score_excludes_weak_matches(memory):
    await memory.remember("notes", "completely unrelated wording", key="a")
    assert await memory.recall("notes", "zzz qqq", min_score=0.99) == []


async def test_forget_removes_one_record(memory):
    await memory.remember("notes", "a", key="a")
    await memory.remember("notes", "b", key="b")
    assert await memory.forget("notes", key="a") == 1
    assert await memory.count("notes") == 1


async def test_clear_empties_one_scope_only(memory):
    await memory.remember("hub", "a", scope="s1")
    await memory.remember("hub", "b", scope="s2")
    await memory.clear("hub", scope="s1")
    assert await memory.count("hub", scope="s1") == 0
    assert await memory.count("hub", scope="s2") == 1


async def test_expired_records_disappear_from_reads(memory):
    await memory.remember("notes", "ephemeral", key="tmp", ttl_seconds=0.05)
    assert await memory.count("notes") == 1
    await asyncio.sleep(0.06)
    assert await memory.count("notes") == 0
    assert await memory.get("notes", key="tmp") is None
    assert await memory.recall("notes", "ephemeral") == []


async def test_purge_expired_reclaims_storage(memory):
    await memory.remember("notes", "ephemeral", key="tmp", ttl_seconds=0.05)
    await asyncio.sleep(0.06)
    assert await memory.purge_expired() == 1
    assert await memory.purge_expired() == 0


async def test_empty_text_is_rejected(memory):
    with pytest.raises(ValueError, match="non-empty"):
        await memory.remember("notes", "")


async def test_projects_lists_everything_written(memory):
    await memory.remember("a", "x")
    await memory.remember("b", "y")
    assert await memory.projects() == ["a", "b"]
