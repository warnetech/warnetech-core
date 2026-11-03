"""Tiered retention for things that grow without bound.

Traces, audit chains and lesson stores all accumulate. Left alone they become
the reason someone disables tracing, and the run you most wanted a record of is
the one where the disk was full.

The tier model is adapted from the hot/warm/ghost policy in
tewartech-node/claude-command-cli, reduced to what a library can honestly
promise: **age decides the tier, the tier decides the fidelity.**

    hot    recent, kept whole            -- you are debugging this right now
    warm   older, kept but compacted     -- shape survives, detail does not
    cold   older still, kept as a digest -- one line per run
    gone   past the horizon              -- deleted

Two rules make this safe to run unattended:

* **An audit chain is never rewritten, only truncated from the front.** Editing
  an entry to compact it would break the hash chain, which is the one property
  the chain exists to provide. Compaction of an audit log therefore means
  *archiving whole entries*, and the archived head digest is preserved so the
  remaining chain still verifies against a recorded anchor.
* **Nothing is deleted without a horizon being set explicitly.** ``max_age_days
  = None`` means keep forever, and that is the default for the audit tier.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Tier = Literal["hot", "warm", "cold", "gone"]


@dataclass(frozen=True, slots=True)
class TierPolicy:
    """How long a tier lasts and what survives in it."""

    name: Tier
    max_age_days: float | None
    keep_fields: tuple[str, ...] = ()
    """Fields retained when compacting. Empty means keep the whole record."""

    @property
    def unbounded(self) -> bool:
        return self.max_age_days is None


DEFAULT_TIERS: tuple[TierPolicy, ...] = (
    TierPolicy("hot", 3.0),
    TierPolicy("warm", 30.0, keep_fields=("name", "kind", "attrs", "error", "duration_ms")),
    TierPolicy("cold", 365.0, keep_fields=("name", "kind", "error")),
    TierPolicy("gone", None),
)

AUDIT_TIERS: tuple[TierPolicy, ...] = (
    # An audit trail is evidence. It is archived, never summarized, and it has
    # no horizon unless the operator sets one.
    TierPolicy("hot", 30.0),
    TierPolicy("warm", 365.0),
    TierPolicy("cold", None),
)


@dataclass(slots=True)
class RetentionReport:
    scanned: int = 0
    kept: int = 0
    compacted: int = 0
    deleted: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    bytes_before: int = 0
    bytes_after: int = 0

    @property
    def bytes_saved(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)

    def render(self) -> str:
        return (
            f"retention: {self.scanned} records -> {self.kept} kept "
            f"({self.compacted} compacted, {self.deleted} deleted), "
            f"{self.bytes_saved} bytes reclaimed, tiers={self.by_tier}"
        )


def tier_for_age(age_days: float, tiers: Iterable[TierPolicy] = DEFAULT_TIERS) -> TierPolicy:
    """The first tier whose window contains this age.

    An unbounded tier terminates the ladder, so a policy ending in an unbounded
    tier never deletes anything.

    >>> tier_for_age(1.0).name
    'hot'
    >>> tier_for_age(400.0).name
    'gone'
    """
    for policy in tiers:
        if policy.unbounded or age_days <= policy.max_age_days:
            return policy
    return TierPolicy("gone", None)


def compact(record: dict[str, Any], policy: TierPolicy) -> dict[str, Any] | None:
    """Reduce one record to what its tier keeps. ``None`` means drop it."""
    if policy.name == "gone":
        return None
    if not policy.keep_fields:
        return record
    reduced = {k: v for k, v in record.items() if k in policy.keep_fields}
    # Always keep enough to place the record in time; a compacted span with no
    # timestamp cannot be re-tiered on the next sweep.
    for key in ("started_at", "at", "seq"):
        if key in record:
            reduced[key] = record[key]
    reduced["_tier"] = policy.name
    return reduced


def sweep_jsonl(
    path: str | Path,
    *,
    tiers: Iterable[TierPolicy] = DEFAULT_TIERS,
    now: float | None = None,
    timestamp_field: str = "started_at",
    archive: str | Path | None = None,
    dry_run: bool = False,
) -> RetentionReport:
    """Apply a retention policy to a JSONL file, in place.

    Records without a usable timestamp are treated as hot and kept -- guessing
    an age for a record you cannot date, and then deleting it, is the one
    outcome that is worse than keeping too much.

    ``archive`` writes everything that would be deleted to a second file first.
    ``dry_run`` reports what would happen and writes nothing, which is the mode
    to run the first time on a log you care about.
    """
    path = Path(path)
    report = RetentionReport()
    if not path.exists():
        return report

    raw = path.read_text(encoding="utf-8")
    report.bytes_before = len(raw.encode("utf-8"))
    reference = time.time() if now is None else now
    tier_list = list(tiers)

    kept_lines: list[str] = []
    dropped: list[str] = []

    for line in raw.splitlines():
        if not line.strip():
            continue
        report.scanned += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # Unparseable lines are kept verbatim: a retention sweep is not the
            # place to silently discard something a reader might still salvage.
            kept_lines.append(line)
            report.kept += 1
            continue

        stamp = record.get(timestamp_field)
        if not isinstance(stamp, int | float):
            kept_lines.append(line)
            report.kept += 1
            report.by_tier["hot"] = report.by_tier.get("hot", 0) + 1
            continue

        age_days = max(0.0, (reference - float(stamp)) / 86400)
        policy = tier_for_age(age_days, tier_list)
        report.by_tier[policy.name] = report.by_tier.get(policy.name, 0) + 1

        reduced = compact(record, policy)
        if reduced is None:
            report.deleted += 1
            dropped.append(line)
            continue

        report.kept += 1
        if reduced is not record:
            report.compacted += 1
        kept_lines.append(json.dumps(reduced, default=str))

    output = "\n".join(kept_lines) + ("\n" if kept_lines else "")
    report.bytes_after = len(output.encode("utf-8"))

    if dry_run:
        return report

    if archive and dropped:
        archive_path = Path(archive)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with archive_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(dropped) + "\n")

    path.write_text(output, encoding="utf-8")
    return report


def sweep_audit(
    path: str | Path,
    *,
    tiers: Iterable[TierPolicy] = AUDIT_TIERS,
    now: float | None = None,
    archive: str | Path | None = None,
    dry_run: bool = False,
) -> RetentionReport:
    """Retention for an audit chain: archive whole entries, never rewrite them.

    Compaction is deliberately unavailable here. Editing an entry to shrink it
    changes its digest and breaks the chain from that point forward, destroying
    the one property the chain exists to provide. Entries past the horizon are
    moved to ``archive`` intact, and the caller keeps the archived head digest
    as the anchor for what remains.
    """
    for policy in tiers:
        if policy.keep_fields:
            raise ValueError(
                f"audit tier {policy.name!r} declares keep_fields; compacting an "
                "audit entry rewrites it and breaks the hash chain"
            )
    return sweep_jsonl(
        path,
        tiers=tiers,
        now=now,
        timestamp_field="at",
        archive=archive,
        dry_run=dry_run,
    )
