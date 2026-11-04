import time

import pytest

from warnetech_core.usage import (
    BudgetExceeded,
    InMemoryUsageStore,
    SQLiteUsageStore,
    UsageRecord,
    UsageTracker,
)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryUsageStore()
    return SQLiteUsageStore(tmp_path / "usage.db")


def seed(store):
    store.record(UsageRecord(project="hub", team="core", model_alias="m1", provider="anthropic",
                             input_tokens=100, output_tokens=50, cost_usd=0.004, latency_ms=100))
    store.record(UsageRecord(project="hub", team="core", model_alias="m2", provider="openai",
                             input_tokens=10, output_tokens=5, cost_usd=0.008, latency_ms=300,
                             ok=False, error="boom"))
    store.record(UsageRecord(project="shop", team="growth", model_alias="m1",
                             provider="anthropic", cost_usd=0.5, latency_ms=200))


def test_summary_groups_and_sorts_by_cost(store):
    seed(store)
    rows = store.summarise(group_by="project")
    assert [r.key for r in rows] == ["shop", "hub"]


def test_summary_counts_tokens_failures_and_latency(store):
    seed(store)
    hub = next(r for r in store.summarise(group_by="project") if r.key == "hub")
    assert (hub.calls, hub.failures, hub.total_tokens) == (2, 1, 165)
    assert hub.cost_usd == pytest.approx(0.012)
    assert hub.avg_latency_ms == pytest.approx(200.0)


def test_summary_can_group_by_team_model_and_provider(store):
    seed(store)
    assert {r.key for r in store.summarise(group_by="team")} == {"core", "growth"}
    assert {r.key for r in store.summarise(group_by="provider")} == {"anthropic", "openai"}
    assert {r.key for r in store.summarise(group_by="model_alias")} == {"m1", "m2"}


def test_missing_group_values_render_as_a_placeholder(store):
    store.record(UsageRecord(project="hub", cost_usd=0.1))
    assert [r.key for r in store.summarise(group_by="model_alias")] == ["-"]


def test_filters_narrow_the_summary(store):
    seed(store)
    rows = store.summarise(group_by="project", project="hub")
    assert [r.key for r in rows] == ["hub"]


def test_time_window_excludes_older_rows(store):
    store.record(UsageRecord(project="hub", cost_usd=1.0, ts=time.time() - 7200))
    store.record(UsageRecord(project="hub", cost_usd=0.5))
    recent = store.summarise(group_by="project", since=time.time() - 3600)
    assert recent[0].cost_usd == pytest.approx(0.5)


def test_recent_returns_newest_first_and_respects_limit(store):
    seed(store)
    rows = store.query(limit=2)
    assert len(rows) == 2
    assert rows[0].ts >= rows[1].ts


def test_records_round_trip_metadata_and_prompt_attribution(store):
    store.record(UsageRecord(project="hub", prompt_id="greet", prompt_version=2,
                             metadata={"prompt_checksum": "abc123"}))
    row = store.query(project="hub")[0]
    assert (row.prompt_id, row.prompt_version) == ("greet", 2)
    assert row.metadata == {"prompt_checksum": "abc123"}


def test_unknown_group_by_is_rejected(store):
    with pytest.raises(ValueError, match="cannot group by"):
        store.summarise(group_by="; DROP TABLE usage")


def test_sqlite_store_survives_reopening(tmp_path):
    path = tmp_path / "usage.db"
    first = SQLiteUsageStore(path)
    first.record(UsageRecord(project="hub", cost_usd=0.25))
    first.close()
    assert SQLiteUsageStore(path).summarise(group_by="project")[0].cost_usd == pytest.approx(0.25)


def test_budget_blocks_a_project_over_its_cap(store):
    tracker = UsageTracker(store, project_budgets_usd={"hub": 0.01})
    tracker.record(UsageRecord(project="hub", cost_usd=0.02))
    with pytest.raises(BudgetExceeded, match="project 'hub'"):
        tracker.check_budget(project="hub")


def test_budget_of_one_project_does_not_block_another(store):
    tracker = UsageTracker(store, project_budgets_usd={"hub": 0.01})
    tracker.record(UsageRecord(project="hub", cost_usd=0.02))
    tracker.check_budget(project="shop")


def test_team_budgets_are_enforced_independently(store):
    tracker = UsageTracker(store, team_budgets_usd={"core": 0.01})
    tracker.record(UsageRecord(project="hub", team="core", cost_usd=0.02))
    with pytest.raises(BudgetExceeded, match="team 'core'"):
        tracker.check_budget(project="hub", team="core")


def test_spend_outside_the_window_does_not_count(store):
    tracker = UsageTracker(store, project_budgets_usd={"hub": 0.01}, budget_window_seconds=60)
    tracker.record(UsageRecord(project="hub", cost_usd=99.0, ts=time.time() - 3600))
    tracker.check_budget(project="hub")
