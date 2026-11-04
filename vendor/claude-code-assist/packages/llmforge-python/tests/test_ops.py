"""Rate limiting, the doctor self-test, retention, and the CLI.

The operational layer: everything that keeps a deployed integration running
rather than making it smarter.
"""

from __future__ import annotations

import json
import time

import pytest

from llmforge import (
    FakeProvider,
    RateLimitExceeded,
    Request,
    TokenBucket,
    doctor,
    sweep_audit,
    sweep_jsonl,
    tier_for_age,
    with_rate_limit,
)
from llmforge.audit import AuditLog
from llmforge.cli import main
from llmforge.retention import AUDIT_TIERS, DEFAULT_TIERS, TierPolicy

# --------------------------------------------------------------------------- #
# rate limiting
# --------------------------------------------------------------------------- #


def test_bucket_drains_then_refills():
    now = [0.0]
    bucket = TokenBucket(requests_per_minute=60, burst=2, clock=lambda: now[0])

    assert [bucket.take("k") for _ in range(3)] == [True, True, False]
    now[0] = 1.0  # 60/min == 1 token/sec
    assert bucket.take("k")


def test_bucket_never_exceeds_burst():
    now = [0.0]
    bucket = TokenBucket(requests_per_minute=600, burst=3, clock=lambda: now[0])
    now[0] = 3600.0  # an hour of refill
    assert bucket.tokens("k") == 3


def test_buckets_are_independent_per_key():
    now = [0.0]
    bucket = TokenBucket(requests_per_minute=60, burst=1, clock=lambda: now[0])
    assert bucket.take("a")
    assert not bucket.take("a")
    assert bucket.take("b")  # a separate key has its own bucket


def test_wait_time_reports_seconds_until_a_token():
    now = [0.0]
    bucket = TokenBucket(requests_per_minute=60, burst=1, clock=lambda: now[0])
    bucket.take("k")
    assert bucket.wait_time("k") == pytest.approx(1.0)


@pytest.mark.parametrize(("rpm", "burst"), [(0, 1), (-1, 1), (60, 0)])
def test_invalid_configuration_is_rejected(rpm, burst):
    with pytest.raises(ValueError):
        TokenBucket(requests_per_minute=rpm, burst=burst)


def test_wrapper_paces_calls_instead_of_letting_them_429():
    slept: list[float] = []
    now = [0.0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    provider = with_rate_limit(
        FakeProvider(["ok"]),
        requests_per_minute=60,
        burst=1,
        sleep=sleep,
        clock=lambda: now[0],
    )
    for _ in range(3):
        provider.complete(Request(messages=[]))

    assert len(slept) == 2
    assert all(s == pytest.approx(1.0) for s in slept)


def test_non_blocking_mode_raises_rather_than_waiting():
    now = [0.0]
    provider = with_rate_limit(
        FakeProvider(["ok"]), requests_per_minute=60, burst=1, block=False, clock=lambda: now[0]
    )
    provider.complete(Request(messages=[]))
    with pytest.raises(RateLimitExceeded) as excinfo:
        provider.complete(Request(messages=[]))
    assert excinfo.value.retryable
    assert excinfo.value.wait_seconds > 0


def test_wait_longer_than_max_wait_raises_instead_of_hanging():
    now = [0.0]
    provider = with_rate_limit(
        FakeProvider(["ok"]),
        requests_per_minute=1,  # 60s per token
        burst=1,
        max_wait=5.0,
        clock=lambda: now[0],
        sleep=lambda _: None,
    )
    provider.complete(Request(messages=[]))
    with pytest.raises(RateLimitExceeded):
        provider.complete(Request(messages=[]))


def test_key_callable_gives_a_bucket_per_model():
    now = [0.0]
    provider = with_rate_limit(
        FakeProvider(["ok"]),
        requests_per_minute=60,
        burst=1,
        key=lambda request: request.model,
        block=False,
        clock=lambda: now[0],
    )
    provider.complete(Request(messages=[], model="claude-opus-5"))
    provider.complete(Request(messages=[], model="claude-haiku-4-5"))  # separate bucket
    with pytest.raises(RateLimitExceeded):
        provider.complete(Request(messages=[], model="claude-opus-5"))


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def test_doctor_runs_everything_offline_and_never_raises():
    report = doctor()
    assert set(report) == {"ok", "failed", "checks", "elapsed_ms"}
    assert report["checks"]["provider_seam"]["ok"]
    assert report["checks"]["assurance_gates"]["ok"]
    assert report["checks"]["audit_chain"]["ok"]
    assert report["checks"]["rate_limiter"]["ok"]


def test_doctor_reports_a_broken_check_instead_of_propagating(monkeypatch):
    """A bug in one check must not take down the report -- that is precisely
    when you need the other ten findings."""
    import sys

    # `from llmforge import doctor` binds the function over the module name, so
    # reach for the module object directly rather than through the package.
    module = sys.modules["llmforge.doctor"]

    def exploding() -> dict:
        raise RuntimeError("check itself is broken")

    monkeypatch.setitem(module.LOCAL_CHECKS, "guardrails", exploding)
    report = module.doctor()

    assert report["checks"]["guardrails"]["ok"] is False
    assert "check itself is broken" in report["checks"]["guardrails"]["error"]
    assert report["checks"]["provider_seam"]["ok"]  # the rest still ran


def test_doctor_reports_a_missing_credential_as_a_finding(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("os.path.expanduser", lambda _p: "/nonexistent-profile-dir")

    result = doctor()["checks"]["credentials"]
    assert result["ok"] is False and result["source"] is None


def test_doctor_never_reports_a_credential_value(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "S" * 30)
    assert "sk-ant" not in json.dumps(doctor(), default=str)


def test_render_produces_readable_output():
    from llmforge import render_doctor

    text = render_doctor(doctor())
    assert "llmforge doctor" in text
    assert "provider_seam" in text


# --------------------------------------------------------------------------- #
# retention
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [(0.0, "hot"), (3.0, "hot"), (3.1, "warm"), (30.0, "warm"), (100.0, "cold"), (400.0, "gone")],
)
def test_tier_boundaries(age_days, expected):
    assert tier_for_age(age_days).name == expected


def test_a_policy_ending_unbounded_never_deletes():
    tiers = (TierPolicy("hot", 1.0), TierPolicy("cold", None))
    assert tier_for_age(10_000.0, tiers).name == "cold"


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_sweep_compacts_warm_and_deletes_past_the_horizon(tmp_path):
    now = time.time()
    log = tmp_path / "trace.jsonl"
    _write_jsonl(
        log,
        [
            {"name": "fresh", "kind": "call", "attrs": {"a": 1}, "id": "x", "started_at": now},
            {"name": "warm", "kind": "call", "attrs": {"a": 2}, "id": "y",
             "started_at": now - 10 * 86400},
            {"name": "ancient", "kind": "call", "attrs": {}, "id": "z",
             "started_at": now - 500 * 86400},
        ],
    )

    report = sweep_jsonl(log, now=now)
    kept = [json.loads(line) for line in log.read_text().splitlines()]

    assert report.deleted == 1
    assert [r["name"] for r in kept] == ["fresh", "warm"]
    assert "id" in kept[0]              # hot keeps everything
    assert "id" not in kept[1]          # warm drops what its tier does not keep
    assert kept[1]["_tier"] == "warm"
    assert "started_at" in kept[1]      # still datable on the next sweep


def test_dry_run_writes_nothing(tmp_path):
    now = time.time()
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [{"name": "old", "kind": "call", "started_at": now - 500 * 86400}])
    before = log.read_text()

    report = sweep_jsonl(log, now=now, dry_run=True)
    assert report.deleted == 1
    assert log.read_text() == before


def test_archive_preserves_what_was_removed(tmp_path):
    now = time.time()
    log = tmp_path / "trace.jsonl"
    archive = tmp_path / "archive.jsonl"
    _write_jsonl(log, [{"name": "old", "kind": "call", "started_at": now - 500 * 86400}])

    sweep_jsonl(log, now=now, archive=archive)
    assert json.loads(archive.read_text().strip())["name"] == "old"


def test_undatable_records_are_kept_not_guessed(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [{"name": "no timestamp", "kind": "call"}])
    report = sweep_jsonl(log, now=time.time())
    assert report.kept == 1 and report.deleted == 0


def test_unparseable_lines_survive(tmp_path):
    log = tmp_path / "trace.jsonl"
    log.write_text("{not json at all\n")
    sweep_jsonl(log, now=time.time())
    assert "not json at all" in log.read_text()


def test_sweep_of_a_missing_file_is_a_no_op(tmp_path):
    assert sweep_jsonl(tmp_path / "nope.jsonl").scanned == 0


def test_audit_sweep_archives_whole_entries_and_leaves_the_chain_verifiable(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(4):
        log.record("agent", "step", {"i": i})
    assert log.verify().ok

    # AUDIT_TIERS is unbounded, so nothing ages out: an audit trail is evidence.
    report = sweep_audit(path, now=time.time() + 10_000 * 86400)
    assert report.deleted == 0
    assert AuditLog(path).verify().ok


def test_audit_tiers_may_not_compact():
    """Rewriting an entry changes its digest and breaks the chain."""
    with pytest.raises(ValueError, match="breaks the hash chain"):
        sweep_audit("ignored.jsonl", tiers=(TierPolicy("hot", 1.0, keep_fields=("actor",)),))


def test_default_and_audit_tiers_differ_in_intent():
    assert any(t.keep_fields for t in DEFAULT_TIERS)     # traces compact
    assert not any(t.keep_fields for t in AUDIT_TIERS)   # audit never does


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def test_cli_doctor_exit_code_reflects_findings(capsys):
    code = main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == (0 if payload["ok"] else 1)


def test_cli_assure_reports_findings_with_exit_1(capsys, monkeypatch):
    diff = (
        "+++ b/src/jwt.py\n@@ -1,3 +1,2 @@\n"
        "-# leeway is 60s; see INC-2231\n def verify(token):\n     pass\n"
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False,
                                                    "read": lambda self: diff})())
    assert main(["assure", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(v["code"] == "surgical.comment-deleted" for v in payload["violations"])


def test_cli_assure_on_a_clean_diff_exits_0(capsys, monkeypatch):
    diff = "+++ b/src/upload.py\n@@ -1,2 +1,3 @@\n def upload():\n+    retry(3)\n"
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False,
                                                    "read": lambda self: diff})())
    assert main(["assure", "--json", "--term", "upload", "--path", "src/"]) == 0


def test_cli_context_lists_what_was_packed(tmp_path, capsys):
    (tmp_path / "auth.py").write_text("def verify_token(t):\n    return True\n")
    assert main(["context", "verify token", "--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "auth.py" in payload["included"]


def test_cli_retention_missing_file_is_an_error_not_a_crash(capsys):
    assert main(["retention", "/nonexistent/path.jsonl"]) == 2


def test_cli_reports_an_unexpected_error_without_a_traceback(capsys, monkeypatch):
    import sys

    monkeypatch.setattr(
        sys.modules["llmforge.doctor"], "doctor",
        lambda **_: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert main(["doctor"]) == 2
    assert "boom" in capsys.readouterr().err
