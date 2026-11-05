"""Signed inbound webhooks: ``POST /v1/webhooks/{name}``.

A chat bridge, automation or script posts ``{"text": "..."}`` and gets the model's reply.
It authenticates with an HMAC, not the service bearer token, so each caller has its own
secret and revoking one does not touch the others.

Signing: ``X-Warnetech-Timestamp`` is Unix seconds. ``X-Warnetech-Signature`` is
``v1=`` plus the hex HMAC-SHA256 of ``"<timestamp>.<raw body>"`` under the caller's secret.

Configuration (environment only):
    WARNETECH_WEBHOOKS                 comma-separated names, e.g. ``ops,notes``
    WARNETECH_WEBHOOK_SECRET_<NAME>    that name's secret, at least 16 characters
    WARNETECH_WEBHOOK_MODEL_<NAME>     optional model alias (default ``chat.default``)

An unknown or unconfigured name is a 404, so names cannot be probed. A request signed
correctly is accepted once: replaying it within the window gets a 409 and the model is
not called again. This is generic: Slack and Discord use their own signing and are not
implemented here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from collections import OrderedDict
from collections.abc import Mapping

from fastapi import APIRouter, HTTPException, Request

from ..gateway import AIGateway

TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 65_536
MAX_TEXT_CHARS = 8000
REPLAY_CAPACITY = 1000
MIN_SECRET_CHARS = 16
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "v1=" + mac.hexdigest()


class ReplayCache:
    """Remembers signatures seen inside the tolerance window; bounded in size."""

    def __init__(self, capacity: int = REPLAY_CAPACITY, ttl: float = TOLERANCE_SECONDS * 2):
        self.capacity, self.ttl = capacity, ttl
        self._seen: OrderedDict[str, float] = OrderedDict()

    def check_and_add(self, key: str, now: float) -> bool:
        """Return True if ``key`` is new (and record it), False if it is a replay."""
        while self._seen and (
            len(self._seen) >= self.capacity or next(iter(self._seen.values())) < now - self.ttl
        ):
            self._seen.popitem(last=False)
        if key in self._seen:
            return False
        self._seen[key] = now
        return True


def configured(env: Mapping[str, str] | None = None) -> dict[str, tuple[str, str]]:
    """Return ``{name: (secret, model)}`` for every usable webhook in the environment."""
    env = os.environ if env is None else env
    found: dict[str, tuple[str, str]] = {}
    for raw in env.get("WARNETECH_WEBHOOKS", "").split(","):
        name = raw.strip().lower()
        if not _NAME_RE.fullmatch(name):
            continue
        secret = env.get(f"WARNETECH_WEBHOOK_SECRET_{name.upper().replace('-', '_')}", "")
        if len(secret) < MIN_SECRET_CHARS:
            continue  # too short to be safe: treated as not configured
        model = env.get(f"WARNETECH_WEBHOOK_MODEL_{name.upper().replace('-', '_')}", "")
        found[name] = (secret, model or "chat.default")
    return found


def verify(
    secret: str, timestamp: str | None, signature: str | None, body: bytes, now: float
) -> str | None:
    """Return None if the request is authentic and fresh, else the reason it is not."""
    if not timestamp or not signature:
        return "missing signature headers"
    try:
        age = abs(now - int(timestamp))
    except ValueError:
        return "bad timestamp"
    if age > TOLERANCE_SECONDS:
        return "timestamp outside the allowed window"
    if not hmac.compare_digest(signature.encode(), sign(secret, timestamp, body).encode()):
        return "bad signature"
    return None


def build_router(get_gateway, clock=time.time) -> APIRouter:
    router = APIRouter()
    replay = ReplayCache()

    @router.post("/v1/webhooks/{name}")
    async def receive(name: str, request: Request) -> dict[str, object]:
        entry = configured().get(name.lower())
        if entry is None:
            raise HTTPException(status_code=404, detail="not found")
        secret, model = entry
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="body too large")
        # Read the stream and stop at the cap, so a huge chunked upload is never buffered.
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="body too large")
            chunks.append(chunk)
        body = b"".join(chunks)
        now = clock()
        problem = verify(
            secret,
            request.headers.get("x-warnetech-timestamp"),
            request.headers.get("x-warnetech-signature"),
            body,
            now,
        )
        if problem:
            raise HTTPException(status_code=401, detail=problem)
        try:
            text = json.loads(body).get("text")
        except Exception:
            raise HTTPException(status_code=400, detail="body must be JSON") from None
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise HTTPException(status_code=400, detail="text must be 1-8000 characters")
        if not replay.check_and_add(request.headers["x-warnetech-signature"], now):
            raise HTTPException(status_code=409, detail="replayed request")
        gateway: AIGateway = get_gateway()
        reply = await gateway.complete(model, text, project=f"webhook-{name.lower()}")
        return {"text": reply.text, "model": reply.model_alias, "tokens": reply.usage.total_tokens}

    return router
