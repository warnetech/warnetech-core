"""Request/response models for the HTTP service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..types import Message


class CompleteBody(BaseModel):
    model: str = "chat.default"
    messages: list[Message] | None = None
    prompt: str | None = Field(default=None, description="Shorthand for one user message.")
    system: str | None = None
    project: str | None = None
    team: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.7
    stop: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromptCompleteBody(BaseModel):
    prompt_id: str
    variables: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None
    model: str | None = None
    project: str | None = None
    team: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.7


class EmbedBody(BaseModel):
    model: str = "embed.default"
    inputs: list[str]
    project: str | None = None
    team: str | None = None


class RememberItem(BaseModel):
    text: str
    key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: float | None = None


class RememberBody(BaseModel):
    project: str
    scope: str | None = None
    items: list[RememberItem]
    embed: bool = True


class RecallBody(BaseModel):
    project: str
    query: str
    limit: int = 5
    scope: str | None = None
    scopes: list[str] | None = None
    also_projects: list[str] | None = Field(
        default=None,
        description="Other projects to search. Omitted means this project only.",
    )
    min_score: float = 0.0
    metadata_filter: dict[str, Any] | None = None


class ContextBody(RecallBody):
    max_tokens: int = 2000
    include_scores: bool = False
