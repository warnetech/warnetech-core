"""warnetech-alloc: rules, classification, scan/ingest/audit and the CLI. Fixed clock throughout."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from warnetech_core.alloc.classify import classify, file_sha256
from warnetech_core.alloc.cli import main
from warnetech_core.alloc.rules import Priority, Rules, Tier, load_rules
from warnetech_core.alloc.store import audit, ingest, scan

NOW = 1_800_000_000.0  # fixed epoch for every test
DAY = 86_400


def make(path: Path, content: bytes, age_days: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    stamp = NOW - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def classify_file(path: Path, rules: Rules | None = None, duplicate: bool = False):
    return classify(path, rules or Rules(), NOW, sha256=file_sha256(path), is_duplicate=duplicate)


# --- rules -----------------------------------------------------------------------------

def test_defaults_match_the_plan_thresholds():
    rules = Rules()
    assert rules.active_days == 14
    assert rules.reference_days == 30


def test_yaml_override_replaces_only_given_keys(tmp_path):
    cfg = tmp_path / "rules.yaml"
    cfg.write_text("active_days: 7\ntier_quota_bytes: {1: 1000}\n", encoding="utf-8")
    rules = load_rules(cfg)
    assert rules.active_days == 7.0
    assert rules.reference_days == 30.0
    assert rules.tier_quota_bytes == {1: 1000}


def test_unknown_rule_key_is_rejected(tmp_path):
    cfg = tmp_path / "rules.yaml"
    cfg.write_text("actve_days: 7\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown keys"):
        load_rules(cfg)


def test_reference_must_exceed_active(tmp_path):
    cfg = tmp_path / "rules.yaml"
    cfg.write_text("active_days: 30\nreference_days: 14\n", encoding="utf-8")
    with pytest.raises(ValueError, match="greater than active_days"):
        load_rules(cfg)


def test_non_positive_threshold_is_rejected(tmp_path):
    cfg = tmp_path / "rules.yaml"
    cfg.write_text("active_days: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        load_rules(cfg)


def test_shipped_example_rules_load():
    example = Path(__file__).resolve().parents[1] / "config" / "priority_rules.yaml"
    assert load_rules(example).active_days == 14


# --- classification ----------------------------------------------------------------------

def test_critical_filename_is_p0_pinned_hot(tmp_path):
    item = classify_file(make(tmp_path / "warnetech-core-notes.md", b"plain", age_days=400))
    assert (item.priority, item.tier) == (Priority.P0, Tier.HOT)


def test_critical_content_marker_is_p0(tmp_path):
    item = classify_file(make(tmp_path / "plan.txt", b"[CRITICAL] rotate the key", age_days=1))
    assert item.priority is Priority.P0
    assert "content carries a critical marker" in item.reasons


def test_transient_download_is_p3_quarantine(tmp_path):
    item = classify_file(make(tmp_path / "setup.exe.part", b"partial", age_days=0))
    assert (item.priority, item.tier) == (Priority.P3, Tier.QUARANTINE)


def test_binary_with_no_reader_is_p3(tmp_path):
    item = classify_file(make(tmp_path / "blob.xyz", b"\x00\x01\x02", age_days=0))
    assert item.priority is Priority.P3


def test_duplicate_content_is_p3(tmp_path):
    item = classify_file(make(tmp_path / "copy.md", b"same", age_days=0), duplicate=True)
    assert item.priority is Priority.P3


def test_recent_file_is_p1_warm(tmp_path):
    item = classify_file(make(tmp_path / "notes.md", b"recent", age_days=1))
    assert (item.priority, item.tier) == (Priority.P1, Tier.WARM)


def test_exactly_active_threshold_is_still_p1(tmp_path):
    # Priority alone cannot show this boundary (the next branch also says P1); check the reason.
    item = classify_file(make(tmp_path / "edge.md", b"x", age_days=14))
    assert item.priority is Priority.P1
    assert any("(active)" in r for r in item.reasons)


def test_between_thresholds_defaults_to_p1_and_says_so(tmp_path):
    item = classify_file(make(tmp_path / "gap.md", b"x", age_days=20))
    assert item.priority is Priority.P1
    assert any("defaulted to P1" in r for r in item.reasons)


def test_exactly_reference_threshold_is_still_p1(tmp_path):
    item = classify_file(make(tmp_path / "edge30.md", b"x", age_days=30))
    assert item.priority is Priority.P1


def test_inactive_known_type_past_reference_is_p2_warm(tmp_path):
    item = classify_file(make(tmp_path / "old.md", b"x", age_days=31))
    assert (item.priority, item.tier) == (Priority.P2, Tier.WARM)


def test_inactive_unknown_type_past_reference_is_p3(tmp_path):
    item = classify_file(make(tmp_path / "old.unknownext", b"x", age_days=31))
    assert (item.priority, item.tier) == (Priority.P3, Tier.QUARANTINE)


def test_content_sniff_reads_no_more_than_sniff_bytes(tmp_path):
    path = make(tmp_path / "big.md", b"a" * 100 + b"[CRITICAL]", age_days=1)
    small = Rules(sniff_bytes=50)
    assert classify_file(path, small).priority is Priority.P1
    assert classify_file(path, Rules()).priority is Priority.P0


# --- scan / ingest / audit ---------------------------------------------------------------

def tree_snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_scan_writes_nothing_and_flags_duplicates(tmp_path):
    src = tmp_path / "src"
    make(src / "a.md", b"same bytes", age_days=1)
    make(src / "b.md", b"same bytes", age_days=1)
    before = tree_snapshot(tmp_path)
    items = scan(src, Rules(), NOW)
    assert tree_snapshot(tmp_path) == before
    assert sorted(i.priority.value for i in items) == ["P1", "P3"]


def test_scan_skips_hidden_files(tmp_path):
    make(tmp_path / "src" / ".secret.md", b"x", age_days=1)
    assert scan(tmp_path / "src", Rules(), NOW) == []


def test_ingest_dry_run_copies_nothing(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make(src / "a.md", b"hello", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=False)
    assert not dest.exists()


def test_ingest_apply_copies_indexes_and_leaves_source_alone(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    original = make(src / "a.md", b"hello", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=True)
    assert original.read_bytes() == b"hello"
    records = [json.loads(line) for line in (dest / "index.jsonl").read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["priority"] == "P1"
    assert (dest / records[0]["dest"]).read_bytes() == b"hello"


def test_reingest_adds_nothing_because_hash_is_known(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make(src / "a.md", b"hello", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=True)
    second = ingest(src, dest, Rules(), NOW, apply=True)
    assert second == []
    assert len((dest / "index.jsonl").read_text().splitlines()) == 1


def test_audit_clean_then_detects_missing_and_changed(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make(src / "a.md", b"hello", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=True)
    assert audit(dest, Rules()).ok

    stored = next(p for p in dest.rglob("*.md"))
    stored.write_bytes(b"tampered")
    assert audit(dest, Rules()).changed == (str(stored),)

    stored.unlink()
    assert len(audit(dest, Rules()).missing) == 1


def test_audit_reports_quota_overrun(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make(src / "a.md", b"0123456789", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=True)
    report = audit(dest, Rules(tier_quota_bytes={1: 5}))
    assert report.over_quota == {1: 10}
    assert not report.ok


def test_audit_counts_corrupt_index_lines_without_failing(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "index.jsonl").write_text("not json\n", encoding="utf-8")
    report = audit(dest, Rules())
    assert report.corrupt_lines == 1
    assert report.total == 0


# --- CLI ----------------------------------------------------------------------------------

def test_cli_scan_returns_zero(tmp_path, capsys):
    make(tmp_path / "src" / "a.md", b"x", age_days=1)
    assert main(["scan", str(tmp_path / "src")]) == 0
    assert "P1" in capsys.readouterr().out


def test_cli_bad_rules_file_returns_usage_error(tmp_path, capsys):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("nonsense: 1\n", encoding="utf-8")
    assert main(["scan", str(tmp_path), "--rules", str(cfg)]) == 2
    assert "unknown keys" in capsys.readouterr().err


def test_cli_audit_exit_code_reflects_problems(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make(src / "a.md", b"hello", age_days=1)
    assert main(["ingest", str(src), "--dest", str(dest), "--apply"]) == 0
    assert main(["audit", "--dest", str(dest)]) == 0
    next(dest.rglob("*.md")).unlink()
    assert main(["audit", "--dest", str(dest)]) == 1


# --- memory link -----------------------------------------------------------------------

from warnetech_core.memory import warnetech_memory  # noqa: E402


def test_p1_text_is_searchable_in_memory(tmp_path):
    src, dest, mem = tmp_path / "src", tmp_path / "dest", tmp_path / "memory.jsonl"
    make(src / "plan.md", b"quokka migration plan for the archive", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=True, memory_path=mem)
    records = warnetech_memory.load(mem).records
    assert len(records) == 1
    hits = warnetech_memory.search(records, "quokka")
    assert hits and "quokka migration plan" in hits[0].record.text
    assert records[0].meta["priority"] == "P1"


def test_p2_is_indexed_by_metadata_only(tmp_path):
    src, dest, mem = tmp_path / "src", tmp_path / "dest", tmp_path / "memory.jsonl"
    make(src / "old.md", b"wombat secret body text", age_days=31)
    ingest(src, dest, Rules(), NOW, apply=True, memory_path=mem)
    record = warnetech_memory.load(mem).records[0]
    assert record.meta["priority"] == "P2"
    assert "wombat" not in record.text
    assert "old.md" in record.text


def test_binary_is_indexed_by_header_only(tmp_path):
    src, dest, mem = tmp_path / "src", tmp_path / "dest", tmp_path / "memory.jsonl"
    make(src / "blob.xyz", b"\x00\x01binarybody", age_days=0)
    ingest(src, dest, Rules(), NOW, apply=True, memory_path=mem)
    record = warnetech_memory.load(mem).records[0]
    assert record.meta["priority"] == "P3"
    assert "binarybody" not in record.text


def test_dry_run_never_touches_memory(tmp_path):
    src, dest, mem = tmp_path / "src", tmp_path / "dest", tmp_path / "memory.jsonl"
    make(src / "a.md", b"hello", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=False, memory_path=mem)
    assert not mem.exists()


def test_reingest_does_not_duplicate_memory_records(tmp_path):
    src, dest, mem = tmp_path / "src", tmp_path / "dest", tmp_path / "memory.jsonl"
    make(src / "a.md", b"hello again", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=True, memory_path=mem)
    ingest(src, dest, Rules(), NOW, apply=True, memory_path=mem)
    assert len(warnetech_memory.load(mem).records) == 1


def test_secret_named_file_is_header_only_in_memory(tmp_path):
    src, dest, mem = tmp_path / "src", tmp_path / "dest", tmp_path / "memory.jsonl"
    make(src / "id_rsa", b"-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=True, memory_path=mem)
    record = warnetech_memory.load(mem).records[0]
    assert "AAAA" not in record.text
    assert "PRIVATE KEY" not in record.text


def test_credential_lines_are_redacted_in_memory(tmp_path):
    src, dest, mem = tmp_path / "src", tmp_path / "dest", tmp_path / "memory.jsonl"
    make(src / "notes.md", b"deploy plan\napi_key = sk-live-123\nsteps follow", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=True, memory_path=mem)
    text = warnetech_memory.load(mem).records[0].text
    assert "sk-live-123" not in text
    assert "api_key = [REDACTED]" in text
    assert "steps follow" in text


def test_private_key_block_in_unnamed_file_stays_out_of_memory(tmp_path):
    src, dest, mem = tmp_path / "src", tmp_path / "dest", tmp_path / "memory.jsonl"
    make(src / "scratch.md", b"-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJ", age_days=1)
    ingest(src, dest, Rules(), NOW, apply=True, memory_path=mem)
    assert "MIIBOgIBAAJ" not in warnetech_memory.load(mem).records[0].text
