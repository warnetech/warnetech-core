"""Local dashboard for warnetech-alloc at ``GET /ui/alloc``.

Same protections as the main UI: loopback-only Host/Origin guard, the service bearer token
when one is set, a hash-based CSP, and ``textContent`` only. It adds three views: the
storage monitor, the priority queue with manual overrides, and a memory inspector.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

from ..alloc.rules import Priority, Rules, Tier, load_rules
from ..alloc.store import OverrideError, audit, current_records, read_index, reallocate
from ..memory import warnetech_memory
from .warnetech_ui import page_csp, read_json, require_local

PAGE = Path(__file__).with_name("alloc_ui.html")
SNIPPET_CHARS = 300
MAX_LIST = 500
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIER_NAMES = {Tier.HOT: "Hot", Tier.WARM: "Warm", Tier.COLD: "Cold", Tier.QUARANTINE: "Quarantine"}


class OverrideBody(BaseModel):
    sha256: str = Field(min_length=64, max_length=64)
    priority: Priority


def _alloc_dir() -> Path:
    return Path(os.environ.get("WARNETECH_ALLOC_DIR", "data/alloc"))


def _rules() -> Rules:
    path = os.environ.get("WARNETECH_ALLOC_RULES")
    try:
        return load_rules(Path(path) if path else None)
    except (ValueError, OSError):
        raise HTTPException(status_code=500, detail="rules file is invalid") from None


def _row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "sha256": record.get("sha256"),
        "name": Path(str(record.get("source", ""))).name,
        "source": record.get("source"),
        "priority": record.get("priority"),
        "tier": record.get("tier"),
        "size": record.get("size"),
        "reasons": record.get("reasons", []),
        "ingested_at": record.get("ingested_at"),
        "override": bool(record.get("override")),
    }


def build_alloc_router(require_token) -> APIRouter:
    router = APIRouter()
    html = PAGE.read_text(encoding="utf-8")
    headers = {
        "Content-Security-Policy": page_csp(html),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }
    guard = [Depends(require_local), Depends(require_token)]

    @router.get("/ui/alloc", response_class=HTMLResponse)
    async def page(request: Request) -> HTMLResponse:
        require_local(request)
        return HTMLResponse(html, headers=headers)

    @router.get("/ui/api/alloc/summary", dependencies=guard)
    async def summary() -> dict[str, Any]:
        dest = _alloc_dir()
        rules = _rules()
        records = current_records(dest)
        files = {int(t): 0 for t in Tier}
        size = {int(t): 0 for t in Tier}
        by_priority = {p.value: 0 for p in Priority}
        for record in records:
            tier = int(record.get("tier", Tier.QUARANTINE))
            files[tier] = files.get(tier, 0) + 1
            size[tier] = size.get(tier, 0) + int(record.get("size") or 0)
            by_priority[str(record.get("priority"))] = (
                by_priority.get(str(record.get("priority")), 0) + 1
            )
        recent = sorted(records, key=lambda r: r.get("updated_at") or r.get("ingested_at") or 0,
                        reverse=True)[:10]
        return {
            "total": len(records),
            "corrupt_lines": read_index(dest / "index.jsonl").corrupt_lines,
            "tiers": [
                {"tier": int(t), "name": _TIER_NAMES[t], "files": files[int(t)],
                 "bytes": size[int(t)], "quota": rules.tier_quota_bytes.get(int(t))}
                for t in Tier
            ],
            "priorities": by_priority,
            "recent": [_row(r) for r in recent],
        }

    @router.get("/ui/api/alloc/files", dependencies=guard)
    async def files(
        priority: str | None = Query(default=None, pattern="^P[0-3]$"),
        limit: int = Query(default=100, ge=1, le=MAX_LIST),
    ) -> dict[str, Any]:
        rows = [r for r in current_records(_alloc_dir())
                if priority is None or r.get("priority") == priority]
        rows.sort(key=lambda r: (str(r.get("priority")), -(r.get("ingested_at") or 0)))
        return {"total": len(rows), "files": [_row(r) for r in rows[:limit]]}

    @router.post("/ui/api/alloc/override", dependencies=guard)
    async def override(request: Request) -> dict[str, Any]:
        try:
            body = OverrideBody.model_validate(await read_json(request, limit=10_000))
        except ValidationError:
            raise HTTPException(
                status_code=400, detail="sha256 and priority (P0-P3) required"
            ) from None
        if not _SHA256.fullmatch(body.sha256):
            raise HTTPException(status_code=400, detail="sha256 must be 64 hex characters")
        try:
            return _row(
                reallocate(
                    _alloc_dir(), body.sha256, body.priority, time.time(),
                    memory_path=warnetech_memory.default_store_path(), rules=_rules(),
                )
            )
        except OverrideError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except TimeoutError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @router.get("/ui/api/alloc/audit", dependencies=guard)
    async def run_audit() -> dict[str, Any]:
        report = audit(_alloc_dir(), _rules())
        return {
            "ok": report.ok,
            "total": report.total,
            "missing": list(report.missing),
            "changed": list(report.changed),
            "unsafe": list(report.unsafe),
            "orphans": list(report.orphans),
            "over_quota": report.over_quota,
            "corrupt_lines": report.corrupt_lines,
            "usage_bytes": report.usage_bytes,
        }

    @router.get("/ui/api/alloc/memory", dependencies=guard)
    async def memory(
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        loaded = warnetech_memory.load(warnetech_memory.default_store_path())
        if q.strip():
            hits = [(h.record, round(h.score, 3))
                    for h in warnetech_memory.search(loaded.records, q, limit)]
        else:
            newest = sorted(loaded.records, key=lambda r: r.ts, reverse=True)[:limit]
            hits = [(r, None) for r in newest]
        return {
            "total": len(loaded.records),
            "skipped_lines": loaded.skipped,
            "hits": [
                {"id": r.id, "source": ", ".join(r.sources), "score": score,
                 "text": r.text[:SNIPPET_CHARS], "meta": r.meta}
                for r, score in hits
            ],
        }

    return router
