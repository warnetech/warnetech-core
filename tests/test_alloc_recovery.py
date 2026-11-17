"""Index safety, crash recovery (audit orphans, repair), batch memory writes, memory sync."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from warnetech_core.alloc import store
from warnetech_core.alloc.cli import main
from warnetech_core.alloc.rules import Priority, Rules
from warnetech_core.alloc.store import (
    audit,
    current_records,
    index_lock,
    ingest,
    read_index,
    reallocate,
    repair,
    scan,
)
from warnetech_core.memory import warnetech_memory

NOW = time.time()


def make(path: Path, content: bytes, age_days: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    stamp = NOW - age_days * 86_400
    os.utime(path, (stamp, stamp))
    return path


def stored_files(dest: Path) -> list[Path]:
    return [p for p in dest.rglob("*") if p.is_file() and p.suffix not in {".jsonl", ".bak"}
            and not p.name.startswith(".")]


@pytest.fixture
def stored(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make(src / "plan.md", b"quokka migration plan")
    ingest(src, dest, Rules(), NOW, apply=True)
    return src, dest


# --- batch memory writes ----------------------------------------------------------------------

def test_add_many_reads_the_store_once(tmp_path, monkeypatch):
    mem = tmp_path / "m.jsonl"
    calls = []
    real_load = warnetech_memory.load
    monkeypatch.setattr(warnetech_memory, "load",
                        lambda p: calls.append(p) or real_load(p))
    warnetech_memory.add_many(mem, [(f"note {i}", f"s{i}", None) for i in range(50)])
    assert len(calls) == 1
    assert len(real_load(mem).records) == 50


def test_add_many_matches_add_for_duplicates_and_sources(tmp_path):
    mem = tmp_path / "m.jsonl"
    results = warnetech_memory.add_many(
        mem, [("Same Text", "a", None), ("same  text", "b", None), ("other", None, {"k": 1})]
    )
    assert [r.status for r in results] == ["stored", "duplicate", "stored"]
    records = warnetech_memory.load(mem).records
    assert len(records) == 2
    first = next(r for r in records if r.text == "Same Text")
    assert first.sources == ("a", "b")
    assert next(r for r in records if r.text == "other").meta == {"k": 1}


def test_add_many_writes_nothing_if_any_note_is_invalid(tmp_path):
    mem = tmp_path / "m.jsonl"
    with pytest.raises(ValueError, match="empty"):
        warnetech_memory.add_many(mem, [("fine", None, None), ("   ", None, None)])
    assert not mem.exists()


def test_add_many_skips_what_the_store_already_holds(tmp_path):
    mem = tmp_path / "m.jsonl"
    warnetech_memory.add(mem, "already here", source="x")
    results = warnetech_memory.add_many(mem, [("already here", "y", None), ("new", None, None)])
    assert [r.status for r in results] == ["duplicate", "stored"]
    assert len(warnetech_memory.load(mem).records) == 2


# --- index lock and durable append ----------------------------------------------------------

def test_lock_blocks_a_second_holder_then_times_out(tmp_path):
    with index_lock(tmp_path), pytest.raises(TimeoutError, match="busy"), index_lock(
        tmp_path, timeout=0.2
    ):
        pass


def test_lock_is_released_after_an_error(tmp_path):
    with pytest.raises(RuntimeError), index_lock(tmp_path):
        raise RuntimeError("boom")
    with index_lock(tmp_path, timeout=0.2):
        pass


def test_a_stale_lock_is_taken_over(tmp_path):
    lock = tmp_path / ".index.lock"
    lock.write_text("")
    old = time.time() - 1000
    os.utime(lock, (old, old))
    with index_lock(tmp_path, timeout=0.5, stale_after=60):
        pass


def test_a_crash_damaged_index_tail_does_not_swallow_the_next_record(stored):
    src, dest = stored
    with (dest / "index.jsonl").open("ab") as handle:
        handle.write(b'{"sha256": "cut off mid-wri')  # no trailing newline
    make(src / "second.md", b"a second file")
    ingest(src, dest, Rules(), NOW, apply=True)
    index = read_index(dest / "index.jsonl")
    assert index.corrupt_lines == 1
    assert len(current_records(dest)) == 2


def test_concurrent_ingests_never_corrupt_the_index(tmp_path):
    dest = tmp_path / "dest"
    errors: list[BaseException] = []

    def work(n: int) -> None:
        try:
            src = tmp_path / f"src{n}"
            for i in range(15):
                make(src / f"f{i}.md", f"worker {n} file {i}".encode())
            ingest(src, dest, Rules(), NOW, apply=True)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    index = read_index(dest / "index.jsonl")
    assert index.corrupt_lines == 0
    assert len(current_records(dest)) == 90
    assert audit(dest, Rules()).ok


def test_scan_stops_hashing_at_max_files(tmp_path, monkeypatch):
    src = tmp_path / "src"
    for i in range(40):
        make(src / f"f{i}.md", f"file {i}".encode())
    hashed = []
    real = store.file_sha256
    monkeypatch.setattr(store, "file_sha256", lambda p: hashed.append(p) or real(p))
    assert len(scan(src, Rules(), NOW, max_files=3)) == 3
    assert len(hashed) == 3


# --- audit orphans and repair -----------------------------------------------------------------

def test_audit_reports_a_stored_file_with_no_record(stored):
    _, dest = stored
    (dest / "tier1_warm" / "stray.txt").write_text("nobody indexed me")
    report = audit(dest, Rules())
    assert [Path(o).name for o in report.orphans] == ["stray.txt"]
    assert not report.ok


def test_repair_relinks_a_file_that_moved_without_an_index_update(stored):
    _, dest = stored
    original = next(p for p in stored_files(dest))
    moved = dest / "tier0_hot" / original.name
    moved.parent.mkdir()
    original.rename(moved)  # simulates a crash after the move, before the index write
    assert len(audit(dest, Rules()).missing) == 1

    before = (dest / "index.jsonl").read_text()
    report = repair(dest, NOW)
    assert [Path(p).name for p in report.relinked] == [moved.name]
    assert (dest / "index.jsonl.bak").read_text() == before
    assert audit(dest, Rules()).ok
    record = current_records(dest)[0]
    assert (record["tier"], record["priority"]) == (0, "P0")


def test_repair_restores_a_lost_copy_from_the_unchanged_original(stored):
    src, dest = stored
    next(p for p in stored_files(dest)).unlink()
    report = repair(dest, NOW)
    assert len(report.restored) == 1
    assert audit(dest, Rules()).ok
    assert (src / "plan.md").read_bytes() == b"quokka migration plan"


def test_repair_will_not_restore_from_a_changed_original(stored):
    src, dest = stored
    next(p for p in stored_files(dest)).unlink()
    (src / "plan.md").write_bytes(b"edited since ingest")
    report = repair(dest, NOW)
    assert report.restored == () and len(report.still_missing) == 1
    assert not audit(dest, Rules()).ok


def test_repair_reindexes_an_orphan_with_priority_from_its_tier_folder(stored):
    _, dest = stored
    orphan = dest / "tier3_quarantine" / "found.bin"
    orphan.parent.mkdir(exist_ok=True)
    orphan.write_bytes(b"\x00unindexed")
    report = repair(dest, NOW)
    assert [Path(p).name for p in report.reindexed] == ["found.bin"]
    record = next(r for r in current_records(dest) if r.get("recovered"))
    assert (record["priority"], record["tier"]) == ("P3", 3)
    assert audit(dest, Rules()).ok


def test_repair_leaves_a_stray_duplicate_alone_and_says_so(stored):
    _, dest = stored
    copy = dest / "tier1_warm" / "copy-of-plan.md"
    copy.write_bytes(b"quokka migration plan")
    report = repair(dest, NOW)
    assert [Path(p).name for p in report.leftover] == ["copy-of-plan.md"]
    assert copy.read_bytes() == b"quokka migration plan"  # nothing deleted
    assert not audit(dest, Rules()).ok


def test_repair_never_touches_an_index_path_outside_the_allocation_dir(stored, tmp_path):
    _, dest = stored
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    evil = {"source": "x", "dest": "../outside.txt", "sha256": "c" * 64, "size": 4,
            "priority": "P1", "tier": 1, "reasons": [], "ingested_at": NOW}
    with (dest / "index.jsonl").open("a") as handle:
        handle.write(json.dumps(evil) + "\n")
    repair(dest, NOW)
    assert outside.read_text() == "keep"
    assert audit(dest, Rules()).unsafe == ("../outside.txt",)


def test_repair_is_a_no_op_on_a_healthy_store(stored):
    _, dest = stored
    report = repair(dest, NOW)
    assert (report.relinked, report.restored, report.reindexed, report.leftover,
            report.still_missing) == ((), (), (), (), ())
    assert audit(dest, Rules()).ok


def test_cli_audit_repair_fixes_and_then_passes(stored, capsys):
    _, dest = stored
    next(p for p in stored_files(dest)).unlink()
    assert main(["audit", "--dest", str(dest)]) == 1
    assert main(["audit", "--dest", str(dest), "--repair"]) == 0
    out = capsys.readouterr().out
    assert "restored from original" in out and "OK" in out


# --- override keeps memory in sync ----------------------------------------------------------

def test_override_replaces_the_memory_record_with_the_new_priority(tmp_path):
    src, dest, mem = tmp_path / "src", tmp_path / "dest", tmp_path / "m.jsonl"
    make(src / "plan.md", b"quokka migration plan")
    ingest(src, dest, Rules(), NOW, apply=True, memory_path=mem)
    digest = current_records(dest)[0]["sha256"]
    reallocate(dest, digest, Priority.P0, NOW, memory_path=mem)
    records = warnetech_memory.load(mem).records
    assert len(records) == 1
    assert (records[0].meta["priority"], records[0].meta["tier"]) == ("P0", 0)
    assert "| P0 |" in records[0].text and "quokka migration plan" in records[0].text


def test_override_without_memory_leaves_memory_untouched(tmp_path):
    src, dest, mem = tmp_path / "src", tmp_path / "dest", tmp_path / "m.jsonl"
    make(src / "plan.md", b"quokka migration plan")
    ingest(src, dest, Rules(), NOW, apply=True, memory_path=mem)
    digest = current_records(dest)[0]["sha256"]
    reallocate(dest, digest, Priority.P0, NOW)
    assert warnetech_memory.load(mem).records[0].meta["priority"] == "P1"
