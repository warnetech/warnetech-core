"""Provider-neutral request/response types.

These are deliberately thin. They exist so that everything above the provider
boundary -- the agent loop, the eval harness, the labs -- can be exercised
against ``FakeProvider`` with no network and no SDK installed.

They are *not* a replacement for the Anthropic SDK's types. When you need the
full fidelity of a response (thinking blocks, citations, container ids), reach
through ``Response.raw``, which always holds the untouched SDK object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Literal

Role = Literal["user", "assistant", "system"]

StopReason = Literal[
    "end_turn",
    "max_tokens",
    "stop_sequence",
    "tool_use",
    "pause_turn",
    "refusal",
]

Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting for a single call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
        )

    @property
    def total_input(self) -> int:
        """Every input token, however it was billed."""
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    @property
    def cache_hit_rate(self) -> float:
        """Share of input tokens served from cache. 0.0 when there is no input."""
        if self.total_input == 0:
            return 0.0
        return self.cache_read_input_tokens / self.total_input


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A single ``tool_use`` block Claude emitted."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The answer to one :class:`ToolCall`, headed back into the transcript."""

    tool_use_id: str
    content: str
    is_error: bool = False

    def to_block(self) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
        }
        if self.is_error:
            block["is_error"] = True
        return block


@dataclass(slots=True)
class Request:
    """Everything needed to make one model call.

    ``system`` and ``messages`` follow the Messages API shapes so that the
    Anthropic adapter is a near-passthrough. Keep the prefix stable across calls
    if you want prompt caching to bite -- see ``docs/02-context-engineering.md``.
    """

    messages: list[dict[str, Any]]
    model: str = "claude-opus-5"
    system: str | list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int = 16000
    effort: Effort | None = None
    thinking: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    tool_choice: dict[str, Any] | None = None
    stop_sequences: list[str] = field(default_factory=list)
    cache: bool = False
    """Set the top-level ``cache_control`` breakpoint on the last cacheable block."""
    stream: bool = False
    """Required by the SDKs whenever ``max_tokens`` is large; the adapter forces it on."""
    metadata: dict[str, Any] = field(default_factory=dict)
    """Free-form tags carried into traces. Never sent to the API."""

    def with_(self, **changes: Any) -> Request:
        """Return a copy with fields replaced. Handy for sweeps and ladders."""
        return replace(self, **changes)


@dataclass(slots=True)
class Response:
    """A normalized model reply."""

    text: str
    stop_reason: StopReason | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    content: list[Any] = field(default_factory=list)
    """The raw content-block list, to be echoed back verbatim into the transcript."""
    refusal_category: str | None = None
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    def json(self) -> Any:
        """Parse ``text`` as JSON. Raises ``ValueError`` with the offending text."""
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:  # pragma: no cover - error path
            preview = self.text[:400]
            raise ValueError(f"response was not valid JSON: {exc}\n---\n{preview}") from exc


class ProviderError(RuntimeError):
    """Raised when a provider call fails in a way the caller must handle."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
