"""Tests for warnetech_core.agi.tool_metrics (Phase 2: metrics, ranking, selection)."""

from __future__ import annotations

import random

import pytest

from warnetech_core.agi.tool_metrics import ToolMetrics


def test_record_counts_outcomes_and_latency():
    m = ToolMetrics()
    m.record("search", success=True, latency_ms=10.0)
    m.record("search", success=False, latency_ms=30.0)
    s = m.stats("search")
    assert (s.successes, s.failures) == (1, 1)
    assert s.mean_latency_ms == pytest.approx(20.0)


def test_unknown_tool_stats_are_zero_and_do_not_register():
    m = ToolMetrics()
    s = m.stats("never_used")
    assert s.trials == 0
    assert s.mean_latency_ms is None
    assert m.tools() == []


def test_negative_latency_is_rejected():
    with pytest.raises(ValueError):
        ToolMetrics().record("t", success=True, latency_ms=-1)


def test_invalid_prior_is_rejected():
    with pytest.raises(ValueError):
        ToolMetrics(prior_alpha=0)


def test_untried_tool_scores_prior_mean():
    assert ToolMetrics().posterior_mean("new") == pytest.approx(0.5)


def test_posterior_mean_follows_beta_update():
    m = ToolMetrics()
    for _ in range(8):
        m.record("good", success=True)
    for _ in range(2):
        m.record("good", success=False)
    # Beta(1 + 8, 1 + 2) -> mean 9 / 12
    assert m.posterior_params("good") == (9.0, 3.0)
    assert m.posterior_mean("good") == pytest.approx(9 / 12)


def test_rank_puts_reliable_tool_first_and_is_stable_on_ties():
    m = ToolMetrics()
    for _ in range(5):
        m.record("reliable", success=True)
    m.record("flaky", success=False)
    # "untried" and "zeta" both sit at the prior mean 0.5 and sort by name.
    assert m.rank(["zeta", "flaky", "untried", "reliable"]) == [
        "reliable",
        "untried",
        "zeta",
        "flaky",
    ]


def test_rank_drops_duplicate_candidates():
    assert ToolMetrics().rank(["a", "a", "b"]) == ["a", "b"]


def test_choose_returns_none_for_no_candidates():
    assert ToolMetrics().choose([]) is None


def test_choose_returns_only_candidate_when_single():
    assert ToolMetrics().choose(["only"], rng=random.Random(0)) == "only"


def test_choose_is_deterministic_with_seeded_rng():
    m = ToolMetrics()
    m.record("a", success=True)
    m.record("b", success=False)
    picks_1 = [m.choose(["a", "b"], rng=random.Random(42)) for _ in range(5)]
    picks_2 = [m.choose(["a", "b"], rng=random.Random(42)) for _ in range(5)]
    assert picks_1 == picks_2


def test_choose_favours_strong_tool_over_many_draws():
    m = ToolMetrics()
    for _ in range(50):
        m.record("strong", success=True)
    for _ in range(50):
        m.record("weak", success=False)
    rng = random.Random(7)
    picks = [m.choose(["strong", "weak"], rng=rng) for _ in range(500)]
    assert picks.count("strong") > 450


def test_choose_still_explores_untried_tool():
    m = ToolMetrics()
    for _ in range(20):
        m.record("known", success=True)
    rng = random.Random(3)
    picks = [m.choose(["known", "untried"], rng=rng) for _ in range(500)]
    assert picks.count("untried") > 0


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "metrics.json"
    m = ToolMetrics()
    m.record("a", success=True, latency_ms=5.0)
    m.record("a", success=False, latency_ms=15.0)
    m.save(path)

    loaded = ToolMetrics.load(path)
    assert loaded.stats("a").trials == 2
    assert loaded.stats("a").mean_latency_ms == pytest.approx(10.0)
    assert loaded.posterior_mean("a") == pytest.approx(m.posterior_mean("a"))


def test_load_missing_file_returns_empty_metrics(tmp_path):
    loaded = ToolMetrics.load(tmp_path / "absent.json")
    assert loaded.tools() == []


def test_load_corrupt_file_raises_state_error(tmp_path):
    from warnetech_core.agi.state import StateError

    path = tmp_path / "metrics.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError):
        ToolMetrics.load(path)
