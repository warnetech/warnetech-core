"""The provider boundary.

One protocol, three implementations:

* :class:`AnthropicProvider` -- the real thing, via the official ``anthropic`` SDK.
* :class:`FakeProvider`      -- scripted replies. Deterministic, offline, free.
* :class:`RecordingProvider` -- wraps another provider and records/replays calls,
  so an eval suite that cost real money once can be re-run for free forever.

Everything above this line in the stack only knows :class:`Provider`.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .types import ProviderError, Request, Response, ToolCall, Usage

# Models that reject `budget_tokens` and accept `output_config.effort`.
# Sending a thinking budget to one of these is a 400, not a soft downgrade.
ADAPTIVE_THINKING_MODELS = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5",
)

# Streaming is required by the SDKs above this many output tokens, or the
# request can die on an HTTP timeout before the model finishes.
STREAM_ABOVE_MAX_TOKENS = 20000


@runtime_checkable
class Provider(Protocol):
    """The single seam between llmforge and any model vendor."""

    name: str

    def complete(self, request: Request) -> Response:  # pragma: no cover - protocol
        ...


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #


class AnthropicProvider:
    """Adapter over the official ``anthropic`` Python SDK.

    The SDK is imported lazily so that installing ``llmforge`` does not force a
    network-capable dependency on anyone who only wants the eval harness or the
    context packer.

    Credentials are resolved by the SDK itself (``ANTHROPIC_API_KEY``, then
    ``ANTHROPIC_AUTH_TOKEN``, then an ``ant auth login`` profile, then workload
    identity federation). Do not pass a key here unless you must inject a
    specific one.
    """

    name = "anthropic"

    def __init__(self, client: Any | None = None, **client_kwargs: Any) -> None:
        if client is not None:
            self._client = client
        else:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on install extras
                raise ProviderError(
                    "AnthropicProvider needs the SDK: pip install 'llmforge[anthropic]'"
                ) from exc
            self._client = anthropic.Anthropic(**client_kwargs)

    # -- request shaping ---------------------------------------------------- #

    @staticmethod
    def build_params(request: Request) -> dict[str, Any]:
        """Translate a :class:`Request` into Messages API kwargs.

        Split out from :meth:`complete` so it can be unit-tested without a
        client, and so you can inspect exactly what would go over the wire.
        """
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": request.messages,
        }
        if request.system is not None:
            params["system"] = request.system
        if request.tools:
            params["tools"] = request.tools
        if request.tool_choice is not None:
            params["tool_choice"] = request.tool_choice
        if request.stop_sequences:
            params["stop_sequences"] = request.stop_sequences
        if request.cache:
            params["cache_control"] = {"type": "ephemeral"}

        output_config: dict[str, Any] = {}
        if request.effort is not None:
            output_config["effort"] = request.effort
        if request.output_schema is not None:
            output_config["format"] = {
                "type": "json_schema",
                "schema": request.output_schema,
            }
        if output_config:
            params["output_config"] = output_config

        if request.thinking is not None:
            thinking = dict(request.thinking)
            if _is_adaptive_model(request.model):
                # `budget_tokens` is removed on these models -- sending it is a 400.
                thinking.pop("budget_tokens", None)
            params["thinking"] = thinking

        return params

    # -- the call ----------------------------------------------------------- #

    def complete(self, request: Request) -> Response:
        params = self.build_params(request)
        use_stream = request.stream or request.max_tokens > STREAM_ABOVE_MAX_TOKENS

        try:
            if use_stream:
                with self._client.messages.stream(**params) as stream:
                    message = stream.get_final_message()
            else:
                message = self._client.messages.create(**params)
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error below
            raise _translate_error(exc) from exc

        return _to_response(message)


def _is_adaptive_model(model: str) -> bool:
    return any(model.startswith(m) for m in ADAPTIVE_THINKING_MODELS)


def _translate_error(exc: Exception) -> ProviderError:
    """Map SDK exceptions onto a retryable/not-retryable decision.

    Imports ``anthropic`` opportunistically: if it is not importable, fall back
    to reading a ``status_code`` attribute off whatever was raised.
    """
    status = getattr(exc, "status_code", None)
    try:
        import anthropic
    except ImportError:  # pragma: no cover - only when SDK absent
        retryable = status in (408, 409, 429) or (status is not None and status >= 500)
        return ProviderError(str(exc), retryable=retryable, status=status)

    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError(str(exc), retryable=True, status=429)
    if isinstance(exc, anthropic.APIConnectionError | anthropic.APITimeoutError):
        return ProviderError(str(exc), retryable=True, status=status)
    if isinstance(exc, anthropic.APIStatusError):
        code = exc.status_code
        return ProviderError(str(exc), retryable=code >= 500 or code in (408, 409), status=code)
    return ProviderError(str(exc), retryable=False, status=status)


def _to_response(message: Any) -> Response:
    """Normalize an SDK ``Message`` into :class:`Response`."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    for block in getattr(message, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))

    raw_usage = getattr(message, "usage", None)
    usage = Usage(
        input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
        output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
    )

    # stop_details is populated only on a refusal; guard before reading it.
    refusal_category = None
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        refusal_category = getattr(details, "category", None)

    return Response(
        text="".join(text_parts),
        stop_reason=stop_reason,
        tool_calls=tool_calls,
        usage=usage,
        model=getattr(message, "model", ""),
        content=list(getattr(message, "content", []) or []),
        refusal_category=refusal_category,
        raw=message,
    )


# --------------------------------------------------------------------------- #
# Fake
# --------------------------------------------------------------------------- #


Script = Response | str | Callable[[Request], "Response | str"]


class FakeProvider:
    """A scripted provider for tests, examples and CI.

    Pass a list of replies. Each is one of:

    * a :class:`Response`  -- returned as-is
    * a ``str``            -- wrapped as an ``end_turn`` text response
    * a callable           -- invoked with the :class:`Request`, result coerced

    When the script runs out the last entry repeats, so a single-element script
    is a constant provider. Every request is recorded on ``.requests``, which is
    usually the thing you actually want to assert on.
    """

    name = "fake"

    def __init__(self, script: Iterable[Script] | Script = ("ok",), *, latency: float = 0.0):
        if isinstance(script, Response | str) or callable(script):
            script = [script]  # type: ignore[list-item]
        self.script: list[Script] = list(script)  # type: ignore[arg-type]
        if not self.script:
            raise ValueError("FakeProvider needs at least one scripted reply")
        self.requests: list[Request] = []
        self.calls = 0
        self.latency = latency

    def complete(self, request: Request) -> Response:
        self.requests.append(request)
        entry = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if self.latency:
            time.sleep(self.latency)

        if callable(entry) and not isinstance(entry, Response):
            entry = entry(request)
        if isinstance(entry, str):
            return Response(
                text=entry,
                stop_reason="end_turn",
                usage=Usage(input_tokens=_estimate(request), output_tokens=len(entry) // 4),
                model=request.model,
            )
        return entry


def _estimate(request: Request) -> int:
    blob = json.dumps(request.messages, default=str) + json.dumps(request.system, default=str)
    return max(1, len(blob) // 4)


def text_response(text: str, **kwargs: Any) -> Response:
    """Build an ``end_turn`` text :class:`Response`. Sugar for fake scripts."""
    kwargs.setdefault("stop_reason", "end_turn")
    return Response(text=text, **kwargs)


def tool_response(*calls: ToolCall, text: str = "", **kwargs: Any) -> Response:
    """Build a ``tool_use`` :class:`Response`. Sugar for fake scripts."""
    kwargs.setdefault("stop_reason", "tool_use")
    return Response(text=text, tool_calls=list(calls), **kwargs)


# --------------------------------------------------------------------------- #
# Record / replay
# --------------------------------------------------------------------------- #


class RecordingProvider:
    """Content-addressed record/replay around any provider.

    An eval suite is only useful if you can re-run it. Paying for every re-run
    means you re-run it rarely, which means it stops catching things. This makes
    the second run free:

    .. code-block:: python

        provider = RecordingProvider(AnthropicProvider(), cassette=".llmforge/evals")

    The cache key is a hash of the fully-shaped request, so changing a prompt by
    one byte correctly forces a real call. In ``replay_only`` mode a miss raises
    instead of reaching the network -- the mode you want in CI.
    """

    name = "recording"

    def __init__(
        self,
        inner: Provider,
        cassette: str | Path = ".llmforge/cassette",
        *,
        replay_only: bool = False,
    ) -> None:
        self.inner = inner
        self.dir = Path(cassette)
        self.replay_only = replay_only
        self.hits = 0
        self.misses = 0

    def _key(self, request: Request) -> str:
        payload = {
            "model": request.model,
            "system": request.system,
            "messages": request.messages,
            "tools": request.tools,
            "max_tokens": request.max_tokens,
            "effort": request.effort,
            "output_schema": request.output_schema,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def complete(self, request: Request) -> Response:
        key = self._key(request)
        path = self.dir / f"{key}.json"
        if path.exists():
            self.hits += 1
            saved = json.loads(path.read_text())
            return Response(
                text=saved["text"],
                stop_reason=saved.get("stop_reason"),
                tool_calls=[ToolCall(**c) for c in saved.get("tool_calls", [])],
                usage=Usage(**saved.get("usage", {})),
                model=saved.get("model", ""),
            )

        self.misses += 1
        if self.replay_only:
            raise ProviderError(f"cassette miss for {key} and replay_only=True")

        response = self.inner.complete(request)
        self.dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "text": response.text,
                    "stop_reason": response.stop_reason,
                    "tool_calls": [
                        {"id": c.id, "name": c.name, "input": c.input}
                        for c in response.tool_calls
                    ],
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cache_creation_input_tokens": (
                            response.usage.cache_creation_input_tokens
                        ),
                        "cache_read_input_tokens": response.usage.cache_read_input_tokens,
                    },
                    "model": response.model,
                },
                indent=2,
            )
        )
        return response


# --------------------------------------------------------------------------- #
# Retry
# --------------------------------------------------------------------------- #


def with_retry(
    provider: Provider,
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Provider:
    """Wrap a provider with jittered exponential backoff on retryable errors.

    The SDK already retries 429/5xx twice. This is the outer layer for long
    agent runs where you would rather wait a minute than lose an hour of work.
    Non-retryable errors (400, 404, auth) propagate immediately -- retrying a
    malformed request just burns time.
    """

    class _Retrying:
        name = f"retry({getattr(provider, 'name', 'provider')})"

        def complete(self, request: Request) -> Response:
            last: ProviderError | None = None
            for attempt in range(attempts):
                try:
                    return provider.complete(request)
                except ProviderError as exc:
                    if not exc.retryable or attempt == attempts - 1:
                        raise
                    last = exc
                    delay = min(max_delay, base_delay * (2**attempt))
                    sleep(delay * (0.5 + random.random() / 2))
            raise last or ProviderError("retry loop exhausted")  # pragma: no cover

    return _Retrying()


def default_provider() -> Provider:
    """The provider the examples and CLI reach for.

    Returns a :class:`FakeProvider` when no credentials are visible, so that
    ``python examples/...`` does something instructive instead of crashing.
    """
    has_creds = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    if not has_creds and not Path(os.path.expanduser("~/.config/anthropic")).exists():
        return FakeProvider(["[FakeProvider] no credentials found -- see .env.example"])
    return with_retry(AnthropicProvider())
