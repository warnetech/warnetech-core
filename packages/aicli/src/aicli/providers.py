"""Providers: the only code that talks to a model.

Each provider is a thin HTTP client over the vendor's own API, written against
urllib so aicli installs with no dependencies. Everything above this module
sees one shape: ``complete(messages, model, max_tokens, ...) -> Completion``.

Tool calling is provider-native on purpose. During a tool loop the message list
holds whatever the provider returned (``Completion.raw_message``) and whatever
``tool_result_messages`` builds, so nothing is lost converting between formats.

A provider raises ``ProviderError`` for anything that should trigger fallback
(unreachable, missing key, 5xx, 429, refusal). The router decides what to do.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .config import ConfigError, ProviderConfig
from .pricing import estimate_tokens

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 120.0
HEALTH_TIMEOUT = 2.0

Message = dict[str, Any]


class ProviderError(Exception):
    """A call failed in a way the router may recover from by falling back."""


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    latency_ms: int
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: Message | None = None
    steps: int = 1


def _http(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("content-type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise ProviderError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"cannot reach {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProviderError(f"non-JSON response from {url}") from exc


def _openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {
        "name": t.name, "description": t.description, "parameters": t.input_schema}}
        for t in tools]


class Provider:
    type = "base"
    default_base = ""

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg
        self.name = cfg.name

    @property
    def base(self) -> str:
        return (self.cfg.base_url or self.default_base).rstrip("/")

    @property
    def host(self) -> str:
        """Where a call goes, for the audit log."""
        return urlparse(self.base).hostname or "offline"

    def complete(self, messages: list[Message], model: str, max_tokens: int,
                 system: str | None = None, params: dict[str, Any] | None = None,
                 tools: list[ToolSpec] | None = None) -> Completion:
        raise NotImplementedError

    def tool_result_messages(self, calls: list[ToolCall], results: list[str]) -> list[Message]:
        return [{"role": "tool", "tool_call_id": c.id, "content": r}
                for c, r in zip(calls, results, strict=True)]

    def available(self) -> tuple[bool, str]:
        """Cheap readiness check. Never raises; the reason is shown to the user."""
        raise NotImplementedError

    def list_models(self) -> list[str]:
        return []

    def _require_key(self) -> str:
        key = self.cfg.api_key()
        if not key:
            raise ProviderError(f"{self.name}: ${self.cfg.api_key_env} is not set")
        return key

    def _key_status(self) -> tuple[bool, str]:
        if not self.cfg.api_key():
            return False, f"${self.cfg.api_key_env} not set"
        return True, "API key present"


class AnthropicProvider(Provider):
    type = "anthropic"
    default_base = "https://api.anthropic.com"

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._require_key(), "anthropic-version": ANTHROPIC_VERSION}

    def complete(self, messages, model, max_tokens, system=None, params=None, tools=None):
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            body["system"] = system
        if params and params.get("temperature") is not None:
            body["temperature"] = params["temperature"]
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description,
                              "input_schema": t.input_schema} for t in tools]
        start = time.monotonic()
        data = _http("POST", f"{self.base}/v1/messages", body, self._headers())
        stop = data.get("stop_reason")
        # A refusal is HTTP 200 with no text: surface it instead of printing "".
        if stop == "refusal":
            raise ProviderError(f"{self.name}: model refused the request")
        blocks = data.get("content", [])
        usage = data.get("usage", {})
        return Completion(
            text="".join(b.get("text", "") for b in blocks if b.get("type") == "text"),
            provider=self.name, model=data.get("model", model),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            stop_reason=stop, latency_ms=int((time.monotonic() - start) * 1000),
            tool_calls=[ToolCall(b["id"], b["name"], b.get("input") or {})
                        for b in blocks if b.get("type") == "tool_use"],
            raw_message={"role": "assistant", "content": blocks},
        )

    def tool_result_messages(self, calls, results):
        # Every result for one turn goes back in ONE user message.
        return [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": r}
            for c, r in zip(calls, results, strict=True)]}]

    def available(self):
        return self._key_status()

    def list_models(self):
        data = _http("GET", f"{self.base}/v1/models?limit=100", headers=self._headers(),
                     timeout=HEALTH_TIMEOUT * 5)
        return [m["id"] for m in data.get("data", [])]


class OpenAIProvider(Provider):
    """OpenAI, or anything that speaks its chat-completions API (set base_url)."""

    type = "openai"
    default_base = "https://api.openai.com/v1"

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._require_key()}"}

    def complete(self, messages, model, max_tokens, system=None, params=None, tools=None):
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        body: dict[str, Any] = {"model": model, "messages": msgs,
                                "max_completion_tokens": max_tokens}
        for key in ("temperature", "seed"):
            if params and params.get(key) is not None:
                body[key] = params[key]
        if tools:
            body["tools"] = _openai_tools(tools)
        start = time.monotonic()
        data = _http("POST", f"{self.base}/chat/completions", body, self._headers())
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage", {})
        calls = []
        for tc in message.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(tc["id"], tc["function"]["name"], args))
        return Completion(
            text=message.get("content") or "", provider=self.name,
            model=data.get("model", model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            stop_reason=choice.get("finish_reason"),
            latency_ms=int((time.monotonic() - start) * 1000),
            tool_calls=calls, raw_message=message or None,
        )

    def available(self):
        return self._key_status()

    def list_models(self):
        data = _http("GET", f"{self.base}/models", headers=self._headers(),
                     timeout=HEALTH_TIMEOUT * 5)
        return sorted(m["id"] for m in data.get("data", []))


class OllamaProvider(Provider):
    type = "ollama"
    default_base = "http://localhost:11434"

    def complete(self, messages, model, max_tokens, system=None, params=None, tools=None):
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        options: dict[str, Any] = {"num_predict": max_tokens}
        for key in ("temperature", "seed"):
            if params and params.get(key) is not None:
                options[key] = params[key]
        body: dict[str, Any] = {"model": model, "messages": msgs, "stream": False,
                                "options": options}
        if tools:
            body["tools"] = _openai_tools(tools)
        start = time.monotonic()
        data = _http("POST", f"{self.base}/api/chat", body)
        message = data.get("message") or {}
        calls = [ToolCall(f"call_{i}", tc["function"]["name"],
                          tc["function"].get("arguments") or {})
                 for i, tc in enumerate(message.get("tool_calls") or [])]
        return Completion(
            text=message.get("content", ""), provider=self.name,
            model=data.get("model", model),
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            stop_reason=data.get("done_reason"),
            latency_ms=int((time.monotonic() - start) * 1000),
            tool_calls=calls, raw_message=message or None,
        )

    def tool_result_messages(self, calls, results):
        return [{"role": "tool", "tool_name": c.name, "content": r}
                for c, r in zip(calls, results, strict=True)]

    def available(self):
        try:
            _http("GET", f"{self.base}/api/tags", timeout=HEALTH_TIMEOUT)
        except ProviderError as exc:
            return False, f"Ollama not reachable at {self.base} ({exc.__cause__ or exc})"
        return True, f"Ollama running at {self.base}"

    def list_models(self):
        data = _http("GET", f"{self.base}/api/tags", timeout=HEALTH_TIMEOUT)
        return [m["name"] for m in data.get("models", [])]


class FakeProvider(Provider):
    """Offline echo, used for demos and tests.

    Options: ``fail = true`` makes every call fail (to exercise fallback);
    ``latency_ms`` sets the reported latency. With tools offered, it calls the
    first tool once (with ``tool_args``), then echoes the tool's result.
    """

    type = "fake"

    @property
    def host(self) -> str:
        return "offline"

    def complete(self, messages, model, max_tokens, system=None, params=None, tools=None):
        if self.cfg.options.get("fail"):
            raise ProviderError(f"{self.name}: configured to fail")
        tool_results = [m for m in messages if m.get("role") == "tool"]
        if tools and not tool_results:
            call = ToolCall("call_0", tools[0].name, dict(self.cfg.options.get("tool_args", {})))
            raw = {"role": "assistant", "content": "",
                   "tool_calls": [{"id": call.id, "type": "function", "function": {
                       "name": call.name, "arguments": json.dumps(call.arguments)}}]}
            return Completion("", self.name, model, 5, 5, "tool_use", 0, [call], raw)
        prompt = tool_results[-1]["content"] if tool_results else \
            next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        text = f"echo: {prompt}"
        return Completion(
            text=text, provider=self.name, model=model,
            input_tokens=estimate_tokens("".join(str(m.get("content", "")) for m in messages)),
            output_tokens=estimate_tokens(text), stop_reason="end_turn",
            latency_ms=int(self.cfg.options.get("latency_ms", 0)),
            raw_message={"role": "assistant", "content": text},
        )

    def available(self):
        if self.cfg.options.get("fail"):
            return False, "configured to fail"
        return True, "offline echo provider"

    def list_models(self):
        return ["echo"]


PROVIDER_TYPES: dict[str, type[Provider]] = {
    cls.type: cls for cls in (AnthropicProvider, OpenAIProvider, OllamaProvider, FakeProvider)
}


def build(cfg: ProviderConfig) -> Provider:
    try:
        return PROVIDER_TYPES[cfg.type](cfg)
    except KeyError:
        raise ConfigError(
            f"provider {cfg.name!r}: unknown type {cfg.type!r} "
            f"(expected one of {sorted(PROVIDER_TYPES)})"
        ) from None
