"""Provider-neutral request/response types.

Everything crossing the gateway boundary is expressed with these models, so a
caller can swap ``anthropic`` for ``openai`` (or a local stub in tests) without
changing a line of downstream code.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    """A single turn in a conversation."""

    role: Role
    content: str

    @classmethod
    def system(cls, content: str) -> Message:
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> Message:
        return cls(role="assistant", content=content)


class CompletionRequest(BaseModel):
    """A chat-completion request, before model-alias resolution."""

    model: str = Field(description="Model alias from the registry, not a raw provider model id.")
    messages: list[Message]
    max_tokens: int = 1024
    temperature: float = 0.7
    stop: list[str] | None = None
    # Attribution — drives cost tracking and memory namespacing.
    project: str = "default"
    team: str | None = None
    # Set by the gateway when the request came from a rendered prompt template.
    prompt_id: str | None = None
    prompt_version: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("messages")
    @classmethod
    def _non_empty(cls, v: list[Message]) -> list[Message]:
        if not v:
            raise ValueError("messages must not be empty")
        return v

    def system_prompt(self) -> str | None:
        """Concatenate leading system messages (Anthropic wants them separately)."""
        parts = [m.content for m in self.messages if m.role == "system"]
        return "\n\n".join(parts) if parts else None

    def conversation(self) -> list[Message]:
        """Messages with system turns stripped out."""
        return [m for m in self.messages if m.role != "system"]


class TokenUsage(BaseModel):
    """Token counts for one call."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class CompletionResponse(BaseModel):
    """A normalised chat-completion result."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    text: str
    model_alias: str
    provider: str
    provider_model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    finish_reason: str | None = None
    attempts: int = 1
    fell_back_from: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    raw: dict[str, Any] | None = None


class EmbeddingRequest(BaseModel):
    """A batch embedding request."""

    model: str
    inputs: list[str]
    project: str = "default"
    team: str | None = None

    @field_validator("inputs")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("inputs must not be empty")
        return v


class EmbeddingResponse(BaseModel):
    """A normalised embedding result."""

    vectors: list[list[float]]
    model_alias: str
    provider: str
    provider_model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    dimensions: int = 0
    attempts: int = 1
    fell_back_from: list[str] = Field(default_factory=list)
