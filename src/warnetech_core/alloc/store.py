"""Scan, ingest, audit and repair. Ingest copies files; it never moves or deletes the source.

Crash safety: every index write is one locked, fsynced append. A crash can leave a stored
file without an index line (an orphan) or an index line whose file moved; ``audit`` reports
both and ``repair`` fixes them, after backing the index up.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .classify import Classification, classify, file_sha256, text_head
from .rules import Priority, Rules, Tier, tier_for

_INDEX = "index.jsonl"
_LOCK = ".index.lock"
_TIER_DIRS = {Tier.HOT: "tier0_hot", Tier.WARM: "tier1_warm", Tier.COLD: "tier2_cold",
              Tier.QUARANTINE: "tier3_quarantine"}
_TIER_BY_DIR = {name: tier for tier, name in _TIER_DIRS.items()}
_PRIORITY_FOR_TIER = {Tier.HOT: Priority.P0, Tier.WARM: Priority.P1, Tier.COLD: Priority.P2,
                      Tier.QUARANTINE: Priority.P3}
_LOCK_TIMEOUT_S = 10.0
_LOCK_STALE_S = 120.0


@contextmanager
def index_lock(dest: Path, timeout: float = _LOCK_TIMEOUT_S,
               stale_after: float = _LOCK_STALE_S) -> Iterator[None]:
    """Cross-platform lock for index writes: an exclusively created file. A lock older than
    ``stale_after`` seconds is treated as left over from a crash and removed."""
    dest.mkdir(parents=True, exist_ok=True)
    lock = dest / _LOCK
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_after:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "the allocation index is busy (another process holds it)"
                ) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _write_index(dest: Path, records: list[dict]) -> None:
    """Append records with one write and one fsync. The caller holds ``index_lock``. A file
    that ends mid-line (a crash) is closed off first so the new lines are not glued on."""
    if not records:
        return
    payload = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records).encode("utf-8")
    with (dest / _INDEX).open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size:
            handle.seek(size - 1)
            if handle.read(1) != b"\n":
                payload = b"\n" + payload
            handle.seek(0, os.SEEK_END)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _walk_files(root: Path, skip: frozenset[Path]) -> Iterator[Path]:
    """Visible regular files under ``root``, lazily, so a file limit stops the walk early."""
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and (current / d).resolve() not in skip
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = current / name
            if path.is_file() and not path.is_symlink():
                yield path


def scan(root: Path, rules: Rules, now: float, *, known_hashes: frozenset[str] = frozenset(),
         skip: frozenset[Path] = frozenset(), max_files: int = 10_000) -> list[Classification]:
    """Classify every visible file under ``root``. Writes nothing."""
    results: list[Classification] = []
    seen: set[str] = set(known_hashes)
    for path in itertools.islice(_walk_files(root.resolve(), skip), max_files):
        digest = file_sha256(path)
        duplicate = digest in seen
        seen.add(digest)
        results.append(classify(path, rules, now, sha256=digest, is_duplicate=duplicate))
    return results


@dataclass
class IndexStats:
    records: list[dict] = field(default_factory=list)
    corrupt_lines: int = 0


def read_index(index_path: Path) -> IndexStats:
    stats = IndexStats()
    if not index_path.exists():
        return stats
    with index_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                stats.corrupt_lines += 1
                continue
            if isinstance(record, dict):
                stats.records.append(record)
            else:
                stats.corrupt_lines += 1
    return stats


def current_records(dest: Path) -> list[dict]:
    """The latest index record for each file (a later record, such as an override, wins)."""
    latest: dict[str, dict] = {}
    for record in read_index(dest / _INDEX).records:
        digest = record.get("sha256")
        if isinstance(digest, str):
            latest.pop(digest, None)
            latest[digest] = record
    return list(latest.values())


def safe_dest_path(dest: Path, relative: object) -> Path | None:
    """``dest / relative`` if it stays inside ``dest``; None for anything else."""
    if not isinstance(relative, str) or not relative:
        return None
    root = dest.resolve()
    candidate = (root / relative).resolve()
    return candidate if candidate.is_relative_to(root) else None


class OverrideError(ValueError):
    """A manual override could not be applied."""


def _item_from_record(record: dict, dest: Path) -> Classification:
    """A Classification for a stored file, for re-indexing it in memory."""
    source = str(record.get("source", ""))
    if not source or source.startswith("("):  # recovered orphans have no original path
        source = str(record.get("dest", ""))
    return Classification(
        path=source,
        size=int(record.get("size") or 0),
        sha256=str(record["sha256"]),
        priority=Priority(record["priority"]),
        tier=Tier(int(record["tier"])),
        reasons=tuple(record.get("reasons", [])),
    )


def reallocate(dest: Path, sha256: str, priority: Priority, now: float, *,
               memory_path: Path | None = None, rules: Rules | None = None) -> dict:
    """Set a stored file's priority by hand and move it to that priority's tier folder.

    Only files already inside ``dest`` are moved. The change is appended to the index, so
    the history stays visible and the latest record wins. With ``memory_path`` the matching
    memory record is replaced, so memory shows the new priority.
    """
    with index_lock(dest):
        record = next((r for r in current_records(dest) if r.get("sha256") == sha256), None)
        if record is None:
            raise OverrideError("no such file in the index")
        source = safe_dest_path(dest, record.get("dest"))
        if source is None or not source.is_file():
            raise OverrideError("stored file is missing or its path is not allowed")
        root = dest.resolve()
        tier = tier_for(priority)
        target = root / _TIER_DIRS[tier] / source.name
        if target != source:
            if target.exists():
                raise OverrideError("a file with that name already exists in the target tier")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        updated = {
            **record,
            "dest": str(target.relative_to(root)),
            "priority": priority.value,
            "tier": int(tier),
            "reasons": [*record.get("reasons", []), "manual override"],
            "override": True,
            "updated_at": now,
        }
        _write_index(dest, [updated])
    if memory_path is not None:
        from .memory_link import resync_item

        item = _item_from_record(updated, dest)
        resync_item(memory_path, item, _head(target, rules or Rules()))
    return updated


def _head(path: Path, rules: Rules) -> str | None:
    try:
        return text_head(path, rules)
    except OSError:
        return None


def ingest(root: Path, dest: Path, rules: Rules, now: float, *, apply: bool,
           max_files: int = 10_000, memory_path: Path | None = None) -> list[Classification]:
    """Plan (apply=False) or copy-and-index (apply=True) files from ``root`` into ``dest``."""
    known = frozenset(
        r["sha256"] for r in read_index(dest / _INDEX).records if isinstance(r.get("sha256"), str)
    )
    skip = frozenset({dest.resolve()})
    found = scan(root, rules, now, known_hashes=known, skip=skip, max_files=max_files)
    # Content already stored (or repeated within this run) is not ingested twice.
    plan: list[Classification] = []
    planned: set[str] = set(known)
    for item in found:
        if item.sha256 in planned:
            continue
        planned.add(item.sha256)
        plan.append(item)
    if not apply:
        return plan

    dest.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for item in plan:
        source = Path(item.path)
        target_dir = dest / _TIER_DIRS[item.tier]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{item.sha256[:12]}-{source.name}"
        if not target.exists():
            shutil.copy2(source, target)
        records.append({
            "source": item.path,
            "dest": str(target.relative_to(dest)),
            "sha256": item.sha256,
            "size": item.size,
            "priority": item.priority.value,
            "tier": int(item.tier),
            "reasons": list(item.reasons),
            "ingested_at": now,
        })
    with index_lock(dest):
        # Another process may have stored some of these while we copied.
        stored = {r.get("sha256") for r in read_index(dest / _INDEX).records}
        fresh = [r for r in records if r["sha256"] not in stored]
        _write_index(dest, fresh)
    if memory_path is not None and fresh:
        from .memory_link import index_items

        wanted = {r["sha256"] for r in fresh}
        index_items(memory_path, [(i, _head(Path(i.path), rules)) for i in plan
                                  if i.sha256 in wanted])
    return plan


@dataclass(frozen=True)
class AuditReport:
    total: int
    missing: tuple[str, ...]
    changed: tuple[str, ...]
    corrupt_lines: int
    usage_bytes: dict[int, int]
    over_quota: dict[int, int]
    unsafe: tuple[str, ...] = ()
    orphans: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.missing or self.changed or self.over_quota or self.unsafe
                    or self.orphans)


def _tier_files(dest: Path) -> list[Path]:
    root = dest.resolve()
    found: list[Path] = []
    for name in _TIER_DIRS.values():
        folder = root / name
        if folder.is_dir():
            found.extend(
                p for p in sorted(folder.rglob("*")) if p.is_file() and not p.is_symlink()
            )
    return found


def audit(dest: Path, rules: Rules) -> AuditReport:
    """Check index entries against disk, find stored files no record points at, and compare
    tier usage with quotas. Read-only."""
    corrupt = read_index(dest / _INDEX).corrupt_lines
    records = current_records(dest)
    missing: list[str] = []
    changed: list[str] = []
    unsafe: list[str] = []
    referenced: set[Path] = set()
    usage: dict[int, int] = {int(t): 0 for t in Tier}
    for record in records:
        target = safe_dest_path(dest, record.get("dest"))
        if target is None:
            unsafe.append(str(record.get("dest")))
            continue
        referenced.add(target)
        tier = int(record.get("tier", Tier.QUARANTINE))
        if not target.is_file():
            missing.append(str(target))
            continue
        usage[tier] = usage.get(tier, 0) + target.stat().st_size
        if file_sha256(target) != record.get("sha256"):
            changed.append(str(target))
    orphans = [str(p) for p in _tier_files(dest) if p not in referenced]
    over = {t: used for t, used in usage.items()
            if t in rules.tier_quota_bytes and used > rules.tier_quota_bytes[t]}
    return AuditReport(len(records), tuple(missing), tuple(changed), corrupt, usage, over,
                       tuple(unsafe), tuple(orphans))


@dataclass(frozen=True)
class RepairReport:
    relinked: tuple[str, ...] = ()
    restored: tuple[str, ...] = ()
    reindexed: tuple[str, ...] = ()
    leftover: tuple[str, ...] = ()
    still_missing: tuple[str, ...] = ()
    backup: str | None = None


def repair(dest: Path, now: float) -> RepairReport:
    """Make the index and the stored files agree again, without deleting anything.

    - A record whose file is gone, but whose content sits elsewhere in the tier folders,
      is relinked to it.
    - A record whose file is gone and whose original still exists unchanged is restored by
      copying that original back.
    - A stored file no record points at is re-indexed, with its priority inferred from its
      tier folder.
    - Anything else is reported and left alone. Unsafe index paths are never touched.

    The index is copied to ``index.jsonl.bak`` first.
    """
    with index_lock(dest):
        index = dest / _INDEX
        backup: str | None = None
        if index.exists():
            shutil.copy2(index, dest / f"{_INDEX}.bak")
            backup = str(dest / f"{_INDEX}.bak")
        root = dest.resolve()
        records = current_records(dest)
        referenced = {p for r in records if (p := safe_dest_path(dest, r.get("dest")))}
        indexed_shas = {r.get("sha256") for r in records}
        orphans = {p: file_sha256(p) for p in _tier_files(dest) if p not in referenced}
        by_sha: dict[str, Path] = {}
        for path, digest in orphans.items():
            by_sha.setdefault(digest, path)

        new_records: list[dict] = []
        relinked: list[str] = []
        restored: list[str] = []
        still_missing: list[str] = []
        used: set[Path] = set()
        for record in records:
            target = safe_dest_path(dest, record.get("dest"))
            if target is None or target.is_file():
                continue
            digest = record.get("sha256")
            found = by_sha.get(digest) if isinstance(digest, str) else None
            if found is not None and found not in used:
                used.add(found)
                tier = _TIER_BY_DIR.get(found.relative_to(root).parts[0], Tier.QUARANTINE)
                keep = int(record.get("tier", -1)) == int(tier)
                new_records.append({
                    **record,
                    "dest": str(found.relative_to(root)),
                    "tier": int(tier),
                    "priority": record.get("priority") if keep else _PRIORITY_FOR_TIER[tier].value,
                    "reasons": [*record.get("reasons", []), "recovered: relinked"],
                    "updated_at": now,
                })
                relinked.append(str(found))
                continue
            original = Path(str(record.get("source", "")))
            if (isinstance(digest, str) and original.is_file() and not original.is_symlink()
                    and file_sha256(original) == digest):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, target)
                restored.append(str(target))
                continue
            still_missing.append(str(target))

        reindexed: list[str] = []
        leftover: list[str] = []
        for path, digest in orphans.items():
            if path in used:
                continue
            if digest in indexed_shas or any(digest == r.get("sha256") for r in new_records):
                leftover.append(str(path))  # a stray duplicate of something already indexed
                continue
            tier = _TIER_BY_DIR.get(path.relative_to(root).parts[0], Tier.QUARANTINE)
            new_records.append({
                "source": "(recovered)",
                "dest": str(path.relative_to(root)),
                "sha256": digest,
                "size": path.stat().st_size,
                "priority": _PRIORITY_FOR_TIER[tier].value,
                "tier": int(tier),
                "reasons": ["recovered orphan; priority inferred from tier folder"],
                "ingested_at": now,
                "recovered": True,
            })
            indexed_shas.add(digest)
            reindexed.append(str(path))

        _write_index(dest, new_records)
    return RepairReport(tuple(relinked), tuple(restored), tuple(reindexed), tuple(leftover),
                        tuple(still_missing), backup)
