"""Append-only record of what the interpreter proposed and ran.

One JSON object per line. Code is stored truncated alongside its SHA-256, so a
long script is still identifiable without bloating the log. The environment and
program output are never written, because either can hold secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

_CODE_PREVIEW_CHARS = 2000


class AuditLog:
    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields: Any) -> None:
        if self.path is None:
            return
        entry: dict[str, Any] = {"ts": round(time.time(), 3), "event": event}
        code = fields.pop("code", None)
        if code is not None:
            entry["code_sha256"] = hashlib.sha256(code.encode("utf-8")).hexdigest()
            entry["code"] = code[:_CODE_PREVIEW_CHARS]
        entry.update(fields)
        # 0o600: the log can contain code the user would not want shared.
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
