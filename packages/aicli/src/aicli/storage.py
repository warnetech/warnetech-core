"""History: every call, local, in one SQLite file you can open with anything.

Failed calls are stored too: a fallback you cannot see later is a fallback you
cannot audit. Full-text search uses FTS5 when the SQLite build has it and falls
back to LIKE when it does not, so search never just breaks.

Columns added after the first release are created on open (see ``MIGRATIONS``),
so an old history file keeps working after an upgrade.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,
    session       TEXT,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    response      TEXT,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd      REAL,
    latency_ms    INTEGER,
    status        TEXT NOT NULL,          -- ok | error
    error         TEXT,
    fallback_from TEXT
);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at);
CREATE INDEX IF NOT EXISTS runs_model ON runs(model);
CREATE INDEX IF NOT EXISTS runs_session ON runs(session);
"""

# column -> DDL type. Append only; never rename or drop.
MIGRATIONS = {"params": "TEXT", "project": "TEXT"}

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS runs_fts USING fts5(
    prompt, response, content='runs', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS runs_ai AFTER INSERT ON runs BEGIN
    INSERT INTO runs_fts(rowid, prompt, response) VALUES (new.id, new.prompt, new.response);
END;
"""

SINCE = {"today": None, "24h": timedelta(hours=24), "7d": timedelta(days=7),
         "30d": timedelta(days=30), "month": None}


def since_iso(spec: str | None, now: datetime | None = None) -> str | None:
    """``today``, ``month``, ``24h``, ``7d``, ``30d``, or None for all time."""
    if not spec:
        return None
    now = now or datetime.now(UTC)
    if spec == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif spec == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif spec in SINCE:
        start = now - SINCE[spec]
    else:
        raise ValueError(f"--since must be one of {', '.join(SINCE)}")
    return start.isoformat(timespec="seconds")


@dataclass
class Run:
    provider: str
    model: str
    prompt: str
    response: str | None
    status: str = "ok"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: int | None = None
    error: str | None = None
    fallback_from: str | None = None
    session: str | None = None
    project: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(runs)")}
        for column, ddl in MIGRATIONS.items():
            if column not in have:
                self.db.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")
        try:
            self.db.executescript(FTS_SCHEMA)
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        row = dict(r)
        row["params"] = json.loads(row["params"]) if row.get("params") else {}
        return row

    def add(self, run: Run) -> int:
        row = dict(run.__dict__, created_at=datetime.now(UTC).isoformat(timespec="seconds"))
        row["params"] = json.dumps(run.params, sort_keys=True) if run.params else None
        cols = ", ".join(row)
        marks = ", ".join(f":{k}" for k in row)
        with self.db:
            cur = self.db.execute(f"INSERT INTO runs ({cols}) VALUES ({marks})", row)
        return cur.lastrowid

    def get(self, run_id: int) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM runs WHERE id = ?", [run_id]).fetchone()
        return self._row(r) if r else None

    def history(self, limit: int = 20, model: str | None = None,
                project: str | None = None) -> list[dict[str, Any]]:
        where, args = [], []
        if model:
            where.append("(model = ? OR provider || '/' || model = ?)")
            args += [model, model]
        if project:
            where.append("project = ?")
            args.append(project)
        sql = "SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY id DESC LIMIT ?"
        return [self._row(r) for r in self.db.execute(sql, [*args, limit])]

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if self.fts:
            # Quote every term so user punctuation is text, not FTS syntax.
            terms = " ".join('"' + t.replace('"', '""') + '"' for t in query.split())
            sql = ("SELECT runs.* FROM runs_fts JOIN runs ON runs.id = runs_fts.rowid "
                   "WHERE runs_fts MATCH ? ORDER BY runs.id DESC LIMIT ?")
            return [self._row(r) for r in self.db.execute(sql, [terms, limit])]
        like = f"%{query}%"
        sql = ("SELECT * FROM runs WHERE prompt LIKE ? OR response LIKE ? "
               "ORDER BY id DESC LIMIT ?")
        return [self._row(r) for r in self.db.execute(sql, [like, like, limit])]

    def stats(self, session: str | None = None, since: str | None = None) -> list[dict[str, Any]]:
        where, args = [], []
        if session:
            where.append("session = ?")
            args.append(session)
        if since:
            where.append("created_at >= ?")
            args.append(since)
        sql = f"""
            SELECT provider, model, COUNT(*) AS calls,
                   SUM(status = 'error') AS errors,
                   SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd) AS cost_usd,
                   SUM(cost_usd IS NULL AND status = 'ok') AS unpriced,
                   CAST(AVG(CASE WHEN status = 'ok' THEN latency_ms END) AS INTEGER)
                       AS avg_latency_ms
            FROM runs {"WHERE " + " AND ".join(where) if where else ""}
            GROUP BY provider, model ORDER BY calls DESC
        """
        return [dict(r) for r in self.db.execute(sql, args)]

    def spent_since(self, since: str) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM runs WHERE created_at >= ?", [since]
        ).fetchone()
        return float(row[0])

    def model_profile(self, provider: str, model: str) -> tuple[int | None, int | None]:
        """Observed (avg latency ms, avg output tokens) of successful calls, if any."""
        row = self.db.execute(
            "SELECT AVG(latency_ms), AVG(output_tokens) FROM runs "
            "WHERE provider = ? AND model = ? AND status = 'ok'", [provider, model]
        ).fetchone()
        return (int(row[0]) if row[0] is not None else None,
                int(row[1]) if row[1] is not None else None)

    def sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        sql = """
            SELECT session, MIN(created_at) AS started, MAX(created_at) AS last,
                   COUNT(*) AS turns, SUM(cost_usd) AS cost_usd,
                   (SELECT prompt FROM runs r2 WHERE r2.session = runs.session
                    ORDER BY id LIMIT 1) AS first_prompt,
                   (SELECT provider || '/' || model FROM runs r3
                    WHERE r3.session = runs.session AND r3.status = 'ok'
                    ORDER BY id DESC LIMIT 1) AS model
            FROM runs WHERE session IS NOT NULL AND status = 'ok'
              AND session NOT LIKE 'cmp-%' AND session NOT LIKE 'batch-%'
            GROUP BY session ORDER BY MAX(id) DESC LIMIT ?
        """
        return [dict(r) for r in self.db.execute(sql, [limit])]

    def conversation(self, session: str) -> list[dict[str, str]]:
        """A chat session's successful turns, rebuilt as messages to resume from."""
        messages: list[dict[str, str]] = []
        for r in self.db.execute("SELECT prompt, response FROM runs WHERE session = ? "
                                 "AND status = 'ok' ORDER BY id", [session]):
            messages += [{"role": "user", "content": r["prompt"]},
                         {"role": "assistant", "content": r["response"] or ""}]
        return messages

    def latest_session(self) -> str | None:
        row = self.db.execute("SELECT session FROM runs WHERE session IS NOT NULL "
                              "AND session NOT LIKE 'cmp-%' AND session NOT LIKE 'batch-%' "
                              "ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else None
