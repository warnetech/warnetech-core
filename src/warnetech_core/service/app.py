"""Optional HTTP service.

Run the gateway centrally when several separately-deployed projects need one
bill and one memory store::

    uvicorn warnetech_core.service.app:app --port 8080

Projects that live in the same process should import
:class:`~warnetech_core.gateway.AIGateway` directly instead — same code path,
no network hop.

Auth is a shared bearer token (``WARNETECH_API_TOKEN``). If the variable is
unset the service refuses to start rather than defaulting to open, because a
gateway holding every provider key is not something to leave unauthenticated.
Set ``WARNETECH_ALLOW_ANONYMOUS=1`` to opt out deliberately for local work.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from ..config import Settings
from ..errors import (
    AllProvidersFailed,
    ConfigError,
    ModelNotRegistered,
    PromptNotFound,
    PromptRenderError,
)
from ..gateway import AIGateway
from ..memory.service import MemoryService
from ..types import Message
from ..usage import BudgetExceeded
from .schemas import (
    CompleteBody,
    ContextBody,
    EmbedBody,
    PromptCompleteBody,
    RecallBody,
    RememberBody,
)

_state: dict[str, Any] = {}


def get_gateway() -> AIGateway:
    return _state["gateway"]


def get_memory() -> MemoryService:
    return _state["memory"]


async def require_token(authorization: str | None = Header(default=None)) -> None:
    """Constant-time bearer check against ``WARNETECH_API_TOKEN``."""
    expected = _state.get("token")
    if expected is None:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    supplied = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid token")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings.from_env()
    token = os.getenv("WARNETECH_API_TOKEN")
    if not token and os.getenv("WARNETECH_ALLOW_ANONYMOUS", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        raise RuntimeError(
            "WARNETECH_API_TOKEN is not set. Set it, or set WARNETECH_ALLOW_ANONYMOUS=1 "
            "if you really intend to expose an unauthenticated gateway."
        )
    gateway = settings.build_gateway()
    _state.update(
        settings=settings,
        gateway=gateway,
        memory=settings.build_memory(gateway),
        token=token,
    )
    try:
        yield
    finally:
        await _state["memory"].aclose()
        await gateway.aclose()
        _state.clear()


app = FastAPI(
    title="warnetech-core",
    version="0.1.0",
    description="Shared AI gateway and memory/context layer.",
    lifespan=lifespan,
)


from .webhooks import build_router as _build_webhook_router  # noqa: E402

app.include_router(_build_webhook_router(get_gateway))


# ---- error translation --------------------------------------------------

@app.exception_handler(AllProvidersFailed)
async def _all_failed(_: Any, exc: AllProvidersFailed):  # noqa: ANN202
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(BudgetExceeded)
async def _budget(_: Any, exc: BudgetExceeded):  # noqa: ANN202
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=402, content={"detail": str(exc)})


@app.exception_handler(PromptNotFound)
@app.exception_handler(ModelNotRegistered)
async def _not_found(_: Any, exc: Exception):  # noqa: ANN202
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(PromptRenderError)
@app.exception_handler(ConfigError)
async def _bad_request(_: Any, exc: Exception):  # noqa: ANN202
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---- meta ---------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, Any]:
    gateway = _state.get("gateway")
    return {
        "status": "ok",
        "version": "0.1.0",
        "providers": sorted(gateway.providers) if gateway else [],
    }


@app.get("/models", dependencies=[Depends(require_token)])
async def list_models(gateway: AIGateway = Depends(get_gateway)) -> dict[str, Any]:
    return {
        "aliases": gateway.registry.aliases(),
        "models": [
            {
                "alias": s.alias,
                "provider": s.provider,
                "provider_model": s.provider_model,
                "modality": s.modality,
                "input_cost_per_mtok": s.input_cost_per_mtok,
                "output_cost_per_mtok": s.output_cost_per_mtok,
                "fallbacks": list(s.fallbacks),
                "available": s.provider in gateway.providers,
            }
            for s in gateway.registry.specs()
        ],
    }


@app.get("/prompts", dependencies=[Depends(require_token)])
async def list_prompts(gateway: AIGateway = Depends(get_gateway)) -> dict[str, Any]:
    return {
        "prompts": [
            {"id": pid, "versions": gateway.prompts.versions(pid)}
            for pid in gateway.prompts.ids()
        ]
    }


@app.get("/prompts/{prompt_id}", dependencies=[Depends(require_token)])
async def get_prompt(
    prompt_id: str,
    version: int | None = None,
    gateway: AIGateway = Depends(get_gateway),
) -> dict[str, Any]:
    return gateway.prompts.get(prompt_id, version).to_dict()


# ---- gateway ------------------------------------------------------------

@app.post("/v1/complete", dependencies=[Depends(require_token)])
async def complete(
    body: CompleteBody, gateway: AIGateway = Depends(get_gateway)
) -> dict[str, Any]:
    if body.messages is None and body.prompt is None:
        raise HTTPException(status_code=422, detail="provide either `messages` or `prompt`")
    messages = body.messages or [Message.user(body.prompt or "")]
    response = await gateway.complete(
        body.model,
        messages,
        project=body.project,
        team=body.team,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
        stop=body.stop,
        system=body.system,
        metadata=body.metadata,
    )
    return response.model_dump(exclude={"raw"})


@app.post("/v1/complete/prompt", dependencies=[Depends(require_token)])
async def complete_prompt(
    body: PromptCompleteBody, gateway: AIGateway = Depends(get_gateway)
) -> dict[str, Any]:
    response = await gateway.complete_prompt(
        body.prompt_id,
        body.variables,
        version=body.version,
        model=body.model,
        project=body.project,
        team=body.team,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
    )
    return response.model_dump(exclude={"raw"})


@app.post("/v1/embed", dependencies=[Depends(require_token)])
async def embed(body: EmbedBody, gateway: AIGateway = Depends(get_gateway)) -> dict[str, Any]:
    response = await gateway.embed(
        body.model, body.inputs, project=body.project, team=body.team
    )
    return response.model_dump()


# ---- usage --------------------------------------------------------------

@app.get("/v1/usage/summary", dependencies=[Depends(require_token)])
async def usage_summary(
    group_by: str = Query("project"),
    project: str | None = None,
    team: str | None = None,
    since: float | None = None,
    gateway: AIGateway = Depends(get_gateway),
) -> dict[str, Any]:
    try:
        summaries = gateway.usage.summary(
            group_by=group_by, project=project, team=team, since=since
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "group_by": group_by,
        "total_cost_usd": round(sum(s.cost_usd for s in summaries), 8),
        "rows": [s.to_dict() for s in summaries],
    }


@app.get("/v1/usage/recent", dependencies=[Depends(require_token)])
async def usage_recent(
    project: str | None = None,
    team: str | None = None,
    limit: int = Query(50, le=1000),
    gateway: AIGateway = Depends(get_gateway),
) -> dict[str, Any]:
    rows = gateway.usage.recent(project=project, team=team, limit=limit)
    return {"rows": [r.to_dict() for r in rows]}


# ---- memory -------------------------------------------------------------

@app.post("/v1/memory/remember", dependencies=[Depends(require_token)])
async def remember(
    body: RememberBody, memory: MemoryService = Depends(get_memory)
) -> dict[str, Any]:
    records = await memory.remember_many(
        body.project,
        [i.model_dump() for i in body.items],
        scope=body.scope,
        embed=body.embed,
    )
    return {"stored": len(records), "records": [r.to_dict() for r in records]}


@app.post("/v1/memory/recall", dependencies=[Depends(require_token)])
async def recall(body: RecallBody, memory: MemoryService = Depends(get_memory)) -> dict[str, Any]:
    hits = await memory.recall(
        body.project,
        body.query,
        limit=body.limit,
        scope=body.scope,
        scopes=body.scopes,
        also_projects=body.also_projects,
        min_score=body.min_score,
        metadata_filter=body.metadata_filter,
    )
    return {"hits": [h.to_dict() for h in hits]}


@app.post("/v1/memory/context", dependencies=[Depends(require_token)])
async def context(body: ContextBody, memory: MemoryService = Depends(get_memory)) -> dict[str, Any]:
    result = await memory.get_context(
        body.project,
        body.query,
        limit=body.limit,
        max_tokens=body.max_tokens,
        scope=body.scope,
        scopes=body.scopes,
        also_projects=body.also_projects,
        min_score=body.min_score,
        metadata_filter=body.metadata_filter,
        include_scores=body.include_scores,
    )
    return result.to_dict()


@app.get("/v1/memory/{project}", dependencies=[Depends(require_token)])
async def list_memory(
    project: str,
    scope: str | None = None,
    limit: int = Query(100, le=1000),
    offset: int = 0,
    memory: MemoryService = Depends(get_memory),
) -> dict[str, Any]:
    records = await memory.list(project, scope=scope, limit=limit, offset=offset)
    return {
        "project": project,
        "count": await memory.count(project, scope=scope),
        "records": [r.to_dict() for r in records],
    }


@app.delete("/v1/memory/{project}", dependencies=[Depends(require_token)])
async def forget(
    project: str,
    key: str | None = None,
    id: str | None = None,
    scope: str | None = None,
    memory: MemoryService = Depends(get_memory),
) -> dict[str, Any]:
    if key is None and id is None:
        raise HTTPException(status_code=422, detail="provide `key` or `id`")
    removed = await memory.forget(project, key=key, id=id, scope=scope)
    return {"removed": removed}
