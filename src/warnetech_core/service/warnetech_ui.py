"""Local web UI: ask a question, upload documents, see the reply and its sources.

Served by the existing service at ``GET /ui``; nothing leaves the machine. Run it bound to
loopback (``--host 127.0.0.1``). On top of that the API routes refuse any request whose
``Host`` or ``Origin`` is not localhost, which blocks DNS-rebinding from a web page, and
they use the service's bearer token when one is set. The page is one HTML file with no
external requests, a CSP that allows only its own inline script and style, and it writes
model and document text with ``textContent`` only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

from ..chat import CHAT_SCOPE, DOCS_SCOPE, SYSTEM, ChatSession
from ..memory.ingest import TEXT_SUFFIXES, ingest_text, is_secret_name

PAGE = Path(__file__).with_name("warnetech_ui.html")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MAX_REQUEST_BYTES = 1_500_000
MAX_UPLOAD_CHARS = 1_000_000
MAX_QUESTION_CHARS = 4000
SNIPPET_CHARS = 300
_PROJECT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]")


def allowed_hosts(env: dict[str, str] | None = None) -> frozenset[str]:
    """Loopback names, plus any in ``WARNETECH_UI_ALLOWED_HOSTS`` (comma separated)."""
    env = os.environ if env is None else env  # type: ignore[assignment]
    extra = {h.strip().lower() for h in env.get("WARNETECH_UI_ALLOWED_HOSTS", "").split(",")}
    return LOCAL_HOSTS | {h for h in extra if h}


def _hostname(value: str) -> str:
    """Host part of a ``Host`` header or an origin, without port, lowercased."""
    parts = urlsplit(value if "//" in value else f"//{value}")
    return (parts.hostname or "").lower()


def require_local(request: Request) -> None:
    """Refuse requests not addressed to, or coming from, a local page."""
    hosts = allowed_hosts()
    if _hostname(request.headers.get("host", "")) not in hosts:
        raise HTTPException(status_code=403, detail="local access only")
    origin = request.headers.get("origin")
    if origin is not None and _hostname(origin) not in hosts:
        raise HTTPException(status_code=403, detail="local access only")


def page_csp(html: str) -> str:
    """A CSP allowing only this page's own inline script and style, by hash."""

    def digest(tag: str) -> str:
        match = re.search(rf"<{tag}>(.*?)</{tag}>", html, re.S)
        body = match.group(1) if match else ""
        return "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"

    return (
        f"default-src 'none'; script-src {digest('script')}; style-src {digest('style')}; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


async def read_json(request: Request, limit: int = MAX_REQUEST_BYTES) -> Any:
    """Read a JSON body, stopping at ``limit`` bytes instead of buffering a huge upload."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise HTTPException(status_code=413, detail="request too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail="request too large")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except ValueError:
        raise HTTPException(status_code=400, detail="body must be JSON") from None


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    project: str = "default"


class UploadBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=MAX_UPLOAD_CHARS)
    project: str = "default"


def _parse(model: type[BaseModel], data: Any) -> Any:
    try:
        parsed = model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise HTTPException(
            status_code=400, detail=f"{'.'.join(map(str, first['loc']))}: {first['msg']}"
        ) from None
    if not _PROJECT_RE.fullmatch(parsed.project):
        raise HTTPException(status_code=400, detail="project: letters, digits, . _ - only")
    return parsed


def clean_upload_name(name: str) -> str:
    """Keep the last path part, drop odd characters, bound the length."""
    base = re.split(r"[\\/]", name)[-1]
    return _NAME_RE.sub("_", base).strip(" .")[:120]


def check_upload(name: str, text: str) -> str:
    """Return the safe stored name, or raise HTTP 400 saying why the file is refused."""
    raw_base = re.split(r"[\\/]", name)[-1]
    if not raw_base or raw_base.startswith("."):
        raise HTTPException(status_code=400, detail="file name not allowed")
    safe = clean_upload_name(name)
    if not safe:
        raise HTTPException(status_code=400, detail="file name not allowed")
    if is_secret_name(raw_base) or is_secret_name(safe):
        raise HTTPException(status_code=400, detail="file may hold secrets; not stored")
    if Path(safe).suffix.lower() not in TEXT_SUFFIXES:
        raise HTTPException(status_code=400, detail="not a supported text type")
    if "\x00" in text:
        raise HTTPException(status_code=400, detail="binary content; not stored")
    if not text.strip():
        raise HTTPException(status_code=400, detail="file is empty")
    return safe


async def answer(gateway: Any, memory: Any, project: str, question: str) -> dict[str, Any]:
    """Answer with retrieved context; return the reply and the sources it was given."""
    session = ChatSession(gateway, memory, project=project, save=False)
    scopes = [memory.default_scope, DOCS_SCOPE, CHAT_SCOPE]
    found = await memory.get_context(project, question, scopes=scopes, include_scores=True)
    parts = [SYSTEM]
    core = await session.core_text()
    if core:
        parts.append(f"<core>\n{core}\n</core>")
    if found.text:
        parts.append(f"<context>\n{found.text}\n</context>")
    parts.append(f"User: {question}\nAssistant:")
    reply = await gateway.complete(session.model, "\n\n".join(parts), project=project)
    sources = [
        {
            "source": hit.record.key or hit.record.id,
            "score": round(hit.score, 3),
            "text": hit.record.text[:SNIPPET_CHARS],
        }
        for hit in found.hits
    ]
    return {
        "text": reply.text,
        "model": reply.model_alias,
        "provider": reply.provider,
        "tokens": reply.usage.total_tokens,
        "sources": sources,
    }


def build_ui_router(get_gateway, get_memory, require_token, auth_required) -> APIRouter:
    router = APIRouter()
    html = PAGE.read_text(encoding="utf-8")
    headers = {
        "Content-Security-Policy": page_csp(html),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }

    @router.get("/ui", response_class=HTMLResponse)
    async def page(request: Request) -> HTMLResponse:
        require_local(request)
        return HTMLResponse(html, headers=headers)

    @router.get("/ui/api/info", dependencies=[Depends(require_local)])
    async def info() -> dict[str, Any]:
        return {"auth_required": bool(auth_required()), "max_upload_chars": MAX_UPLOAD_CHARS}

    @router.post("/ui/api/ask", dependencies=[Depends(require_local), Depends(require_token)])
    async def ask(request: Request) -> dict[str, Any]:
        body = _parse(AskBody, await read_json(request))
        return await answer(get_gateway(), get_memory(), body.project, body.question)

    @router.post("/ui/api/upload", dependencies=[Depends(require_local), Depends(require_token)])
    async def upload(request: Request) -> dict[str, Any]:
        body = _parse(UploadBody, await read_json(request))
        safe = check_upload(body.name, body.text)
        chunks, removed = await ingest_text(get_memory(), body.project, safe, body.text)
        return {"name": safe, "chunks": chunks, "removed": removed}

    return router
