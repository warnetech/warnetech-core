"""Scan, ingest and audit. Ingest copies files; it never moves or deletes the source."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .classify import Classification, classify, file_sha256, text_head
from .rules import Priority, Rules, Tier, tier_for

_TIER_DIRS = {Tier.HOT: "tier0_hot", Tier.WARM: "tier1_warm", Tier.COLD: "tier2_cold",
              Tier.QUARANTINE: "tier3_quarantine"}


def _walk_files(root: Path, skip: frozenset[Path]) -> list[Path]:
    found: list[Path] = []
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
                found.append(path)
    return found


def scan(root: Path, rules: Rules, now: float, *, known_hashes: frozenset[str] = frozenset(),
         skip: frozenset[Path] = frozenset(), max_files: int = 10_000) -> list[Classification]:
    """Classify every visible file under ``root``. Writes nothing."""
    results: list[Classification] = []
    seen: set[str] = set(known_hashes)
    for path in _walk_files(root.resolve(), skip)[:max_files]:
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
    with index_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                stats.records.append(json.loads(line))
            except json.JSONDecodeError:
                stats.corrupt_lines += 1
    return stats


def current_records(dest: Path) -> list[dict]:
    """The latest index record for each file (a later record, such as an override, wins)."""
    latest: dict[str, dict] = {}
    for record in read_index(dest / "index.jsonl").records:
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


def reallocate(dest: Path, sha256: str, priority: Priority, now: float) -> dict:
    """Set a stored file's priority by hand and move it to that priority's tier folder.

    Only files already inside ``dest`` are moved. The change is appended to the index, so
    the history stays visible and the latest record wins.
    """
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
    with (dest / "index.jsonl").open("a", encoding="utf-8") as index:
        index.write(json.dumps(updated, sort_keys=True) + "\n")
    return updated


def ingest(root: Path, dest: Path, rules: Rules, now: float, *, apply: bool,
           max_files: int = 10_000, memory_path: Path | None = None) -> list[Classification]:
    """Plan (apply=False) or copy-and-index (apply=True) files from ``root`` into ``dest``."""
    index_path = dest / "index.jsonl"
    known = frozenset(
        r["sha256"] for r in read_index(index_path).records if isinstance(r.get("sha256"), str)
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
    with index_path.open("a", encoding="utf-8") as index:
        for item in plan:
            source = Path(item.path)
            target_dir = dest / _TIER_DIRS[item.tier]
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{item.sha256[:12]}-{source.name}"
            if not target.exists():
                shutil.copy2(source, target)
            if memory_path is not None:
                from .memory_link import index_item

                index_item(memory_path, item, text_head(source, rules))
            record = {
                "source": item.path,
                "dest": str(target.relative_to(dest)),
                "sha256": item.sha256,
                "size": item.size,
                "priority": item.priority.value,
                "tier": int(item.tier),
                "reasons": list(item.reasons),
                "ingested_at": now,
            }
            index.write(json.dumps(record, sort_keys=True) + "\n")
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

    @property
    def ok(self) -> bool:
        return not (self.missing or self.changed or self.over_quota or self.unsafe)


def audit(dest: Path, rules: Rules) -> AuditReport:
    """Check index entries against disk, and tier usage against quotas. Read-only."""
    corrupt = read_index(dest / "index.jsonl").corrupt_lines
    records = current_records(dest)
    missing: list[str] = []
    changed: list[str] = []
    unsafe: list[str] = []
    usage: dict[int, int] = {int(t): 0 for t in Tier}
    for record in records:
        target = safe_dest_path(dest, record.get("dest"))
        if target is None:
            unsafe.append(str(record.get("dest")))
            continue
        tier = int(record.get("tier", Tier.QUARANTINE))
        if not target.is_file():
            missing.append(str(target))
            continue
        usage[tier] = usage.get(tier, 0) + target.stat().st_size
        if file_sha256(target) != record.get("sha256"):
            changed.append(str(target))
    over = {t: used for t, used in usage.items()
            if t in rules.tier_quota_bytes and used > rules.tier_quota_bytes[t]}
    return AuditReport(len(records), tuple(missing), tuple(changed), corrupt, usage, over,
                       tuple(unsafe))
