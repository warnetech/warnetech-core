"""Token and cost accounting, per project and per team.

Every call through the gateway lands here — successes and failures alike, since
a failed call after two retries still burned wall-clock and sometimes tokens.
The default store is SQLite so a mini-project gets attribution with zero setup;
point ``UsageTracker`` at a shared database (or a Postgres-backed store) once
several projects need one bill.
"""

from __future__ import annotations

import abc
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UsageRecord:
    """One accounted call."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)
    project: str = "default"
    team: str | None = None
    operation: str = "complete"  # complete | embed
    model_alias: str = ""
    provider: str = ""
    provider_model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    attempts: int = 1
    ok: bool = True
    error: str | None = None
    prompt_id: str | None = None
    prompt_version: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UsageSummary:
    """Aggregated usage for one grouping key."""

    key: str
    calls: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    avg_latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "total_tokens": self.total_tokens}


class UsageStore(abc.ABC):
    """Where usage records are persisted."""

    @abc.abstractmethod
    def record(self, record: UsageRecord) -> None: ...

    @abc.abstractmethod
    def query(
        self,
        *,
        project: str | None = None,
        team: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[UsageRecord]: ...

    @abc.abstractmethod
    def summarise(
        self,
        *,
        group_by: str = "project",
        project: str | None = None,
        team: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[UsageSummary]: ...

    def close(self) -> None:
        return None


class InMemoryUsageStore(UsageStore):
    """Ring buffer. Fine for tests and short-lived processes; nothing survives a restart."""

    def __init__(self, max_records: int = 100_000) -> None:
        self._records: list[UsageRecord] = []
        self._max = max_records
        self._lock = threading.Lock()

    def record(self, record: UsageRecord) -> None:
        with self._lock:
            self._records.append(record)
            if len(self._records) > self._max:
                del self._records[: len(self._records) - self._max]

    def _filter(
        self,
        project: str | None,
        team: str | None,
        since: float | None,
        until: float | None,
    ) -> list[UsageRecord]:
        with self._lock:
            rows = list(self._records)
        return [
            r
            for r in rows
            if (project is None or r.project == project)
            and (team is None or r.team == team)
            and (since is None or r.ts >= since)
            and (until is None or r.ts <= until)
        ]

    def query(
        self,
        *,
        project: str | None = None,
        team: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[UsageRecord]:
        rows = self._filter(project, team, since, until)
        rows.sort(key=lambda r: r.ts, reverse=True)
        return rows[:limit]

    def summarise(
        self,
        *,
        group_by: str = "project",
        project: str | None = None,
        team: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[UsageSummary]:
        rows = self._filter(project, team, since, until)
        return _summarise_records(rows, group_by)


_GROUPABLE = frozenset(
    {"project", "team", "model_alias", "provider", "operation", "prompt_id"}
)


def _group_key(record: UsageRecord, group_by: str) -> str:
    return str(getattr(record, group_by) or "-")


def _summarise_records(rows: Iterable[UsageRecord], group_by: str) -> list[UsageSummary]:
    if group_by not in _GROUPABLE:
        raise ValueError(f"cannot group by {group_by!r}; try one of {sorted(_GROUPABLE)}")
    buckets: dict[str, UsageSummary] = {}
    latency_totals: dict[str, float] = {}
    for record in rows:
        key = _group_key(record, group_by)
        summary = buckets.setdefault(key, UsageSummary(key=key))
        summary.calls += 1
        summary.failures += 0 if record.ok else 1
        summary.input_tokens += record.input_tokens
        summary.output_tokens += record.output_tokens
        summary.cost_usd += record.cost_usd
        latency_totals[key] = latency_totals.get(key, 0.0) + record.latency_ms
    for key, summary in buckets.items():
        summary.cost_usd = round(summary.cost_usd, 8)
        summary.avg_latency_ms = round(latency_totals[key] / summary.calls, 2)
    return sorted(buckets.values(), key=lambda s: s.cost_usd, reverse=True)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    project TEXT NOT NULL,
    team TEXT,
    operation TEXT NOT NULL,
    model_alias TEXT,
    provider TEXT,
    provider_model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 1,
    ok INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    prompt_id TEXT,
    prompt_version INTEGER,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS usage_project_ts ON usage (project, ts);
CREATE INDEX IF NOT EXISTS usage_team_ts ON usage (team, ts);
CREATE INDEX IF NOT EXISTS usage_ts ON usage (ts);
"""


class SQLiteUsageStore(UsageStore):
    """Durable store with no server to run. The default for local projects."""

    def __init__(self, path: str | Path = "warnetech_usage.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + an explicit lock: the gateway may write from
        # whichever thread the event loop happens to be running on.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def record(self, record: UsageRecord) -> None:
        import json

        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO usage (
                    id, ts, project, team, operation, model_alias, provider,
                    provider_model, input_tokens, output_tokens, cost_usd,
                    latency_ms, attempts, ok, error, prompt_id, prompt_version, metadata
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.id,
                    record.ts,
                    record.project,
                    record.team,
                    record.operation,
                    record.model_alias,
                    record.provider,
                    record.provider_model,
                    record.input_tokens,
                    record.output_tokens,
                    record.cost_usd,
                    record.latency_ms,
                    record.attempts,
                    1 if record.ok else 0,
                    record.error,
                    record.prompt_id,
                    record.prompt_version,
                    json.dumps(record.metadata) if record.metadata else None,
                ),
            )
            self._conn.commit()

    def _where(
        self,
        project: str | None,
        team: str | None,
        since: float | None,
        until: float | None,
    ) -> tuple[str, list[Any]]:
        clauses, params = [], []
        if project is not None:
            clauses.append("project = ?")
            params.append(project)
        if team is not None:
            clauses.append("team = ?")
            params.append(team)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if until is not None:
            clauses.append("ts <= ?")
            params.append(until)
        return ("WHERE " + " AND ".join(clauses) if clauses else "", params)

    def query(
        self,
        *,
        project: str | None = None,
        team: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[UsageRecord]:
        import json

        where, params = self._where(project, team, since, until)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM usage {where} ORDER BY ts DESC LIMIT ?", [*params, limit]
            ).fetchall()
        return [
            UsageRecord(
                id=row["id"],
                ts=row["ts"],
                project=row["project"],
                team=row["team"],
                operation=row["operation"],
                model_alias=row["model_alias"] or "",
                provider=row["provider"] or "",
                provider_model=row["provider_model"] or "",
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                cost_usd=row["cost_usd"],
                latency_ms=row["latency_ms"],
                attempts=row["attempts"],
                ok=bool(row["ok"]),
                error=row["error"],
                prompt_id=row["prompt_id"],
                prompt_version=row["prompt_version"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            )
            for row in rows
        ]

    def summarise(
        self,
        *,
        group_by: str = "project",
        project: str | None = None,
        team: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[UsageSummary]:
        if group_by not in _GROUPABLE:
            raise ValueError(f"cannot group by {group_by!r}; try one of {sorted(_GROUPABLE)}")
        where, params = self._where(project, team, since, until)
        # group_by is validated against a fixed allowlist above, so interpolating
        # it into the column list here cannot inject.
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT COALESCE(NULLIF({group_by}, ''), '-') AS key,
                       COUNT(*) AS calls,
                       SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failures,
                       SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(cost_usd) AS cost_usd,
                       AVG(latency_ms) AS avg_latency_ms
                FROM usage {where}
                GROUP BY key
                ORDER BY cost_usd DESC
                """,
                params,
            ).fetchall()
        return [
            UsageSummary(
                key=row["key"],
                calls=row["calls"],
                failures=row["failures"] or 0,
                input_tokens=row["input_tokens"] or 0,
                output_tokens=row["output_tokens"] or 0,
                cost_usd=round(row["cost_usd"] or 0.0, 8),
                avg_latency_ms=round(row["avg_latency_ms"] or 0.0, 2),
            )
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class BudgetExceeded(Exception):
    """A project or team blew through its configured spend cap."""

    def __init__(self, scope: str, name: str, spent: float, limit: float) -> None:
        self.scope, self.name, self.spent, self.limit = scope, name, spent, limit
        super().__init__(
            f"{scope} {name!r} has spent ${spent:.4f} of its ${limit:.4f} window budget"
        )


class UsageTracker:
    """Writes usage records and enforces optional spend caps."""

    def __init__(
        self,
        store: UsageStore | None = None,
        *,
        project_budgets_usd: dict[str, float] | None = None,
        team_budgets_usd: dict[str, float] | None = None,
        budget_window_seconds: float = 86_400.0,
    ) -> None:
        self.store = store or InMemoryUsageStore()
        self.project_budgets = dict(project_budgets_usd or {})
        self.team_budgets = dict(team_budgets_usd or {})
        self.budget_window = budget_window_seconds

    def record(self, record: UsageRecord) -> UsageRecord:
        self.store.record(record)
        return record

    def spent(
        self, *, project: str | None = None, team: str | None = None, window: float | None = None
    ) -> float:
        """Total USD spent in the trailing window for a project or team."""
        since = time.time() - (window if window is not None else self.budget_window)
        summaries = self.store.summarise(
            group_by="project" if project is not None else "team",
            project=project,
            team=team,
            since=since,
        )
        return round(sum(s.cost_usd for s in summaries), 8)

    def check_budget(self, *, project: str, team: str | None = None) -> None:
        """Raise if the caller is over budget.

        Called *before* dispatch, so the check is on spend already recorded —
        a single call can still overshoot the cap, which is the right trade for
        not having to predict a response's token count.
        """
        limit = self.project_budgets.get(project)
        if limit is not None:
            spent = self.spent(project=project)
            if spent >= limit:
                raise BudgetExceeded("project", project, spent, limit)
        if team is not None:
            team_limit = self.team_budgets.get(team)
            if team_limit is not None:
                spent = self.spent(team=team)
                if spent >= team_limit:
                    raise BudgetExceeded("team", team, spent, team_limit)

    def summary(self, **kwargs: Any) -> list[UsageSummary]:
        return self.store.summarise(**kwargs)

    def recent(self, **kwargs: Any) -> list[UsageRecord]:
        return self.store.query(**kwargs)
