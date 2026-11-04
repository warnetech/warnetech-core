"""Audit log: one JSON line for every request that leaves aicli.

The history database answers "what did I ask and what came back". This log
answers "what left my machine, to where, and when". So it records destinations,
sizes and hashes, never prompt text: you can hand the audit log to a security
reviewer without handing over your prompts.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class AuditLog:
    def __init__(self, path: Path):
        self.path = path

    def write(self, event: str, **fields: Any) -> None:
        record = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "event": event, **fields}
        line = json.dumps(record, sort_keys=True, default=str)
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def read(self, limit: int | None = None, cloud_only: bool = False) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn final line from a crash must not hide the rest
            if cloud_only and row.get("local", True):
                continue
            rows.append(row)
        return rows[-limit:] if limit else rows
