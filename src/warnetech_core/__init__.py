"""warnetech-core — shared AI gateway and memory/context layer.

Two things, so that no Warnetech project has to build either again:

1. :class:`~warnetech_core.gateway.AIGateway` — every model call goes through
   one place that owns model abstraction, versioned prompts, cost tracking,
   retry, rate limiting and cross-provider fallback.
2. :class:`~warnetech_core.memory.MemoryService` — namespaced memory with a
   one-call retrieval API, over a pluggable store.

Quick start::

    from warnetech_core import build, Message

    gateway, memory = build(project="hub-notes")
    await memory.remember("hub-notes", "We chose Postgres for billing.", key="adr-1")
    context = await memory.get_context("hub-notes", "which database for billing?")
    reply = await gateway.complete("chat.default", f"{context.text}\\n\\nWhich DB?")
"""

from .config import Settings, build
from .errors import (
    AllProvidersFailed,
    ConfigError,
    ModelNotRegistered,
    PromptNotFound,
    PromptRenderError,
    ProviderError,
    RateLimitError,
    WarnetechError,
)
from .gateway import AIGateway
from .integrations import make_embed_fn
from .memory import (
    ContextResult,
    ContextStore,
    InMemoryContextStore,
    MemoryRecord,
    MemoryService,
    Namespace,
    SearchHit,
    SQLiteContextStore,
)
from .prompts import PromptRegistry, PromptTemplate
from .providers import EchoProvider, Provider
from .registry import ModelRegistry, ModelSpec
from .resilience import RateLimiterRegistry, RetryPolicy, TokenBucket
from .sync import SyncRunner
from .types import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    Message,
    TokenUsage,
)
from .usage import (
    BudgetExceeded,
    InMemoryUsageStore,
    SQLiteUsageStore,
    UsageRecord,
    UsageSummary,
    UsageTracker,
)

__version__ = "0.1.0"

__all__ = [
    "AIGateway",
    "AllProvidersFailed",
    "BudgetExceeded",
    "CompletionRequest",
    "CompletionResponse",
    "ConfigError",
    "ContextResult",
    "ContextStore",
    "EchoProvider",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "InMemoryContextStore",
    "InMemoryUsageStore",
    "MemoryRecord",
    "MemoryService",
    "Message",
    "ModelNotRegistered",
    "ModelRegistry",
    "ModelSpec",
    "Namespace",
    "PromptNotFound",
    "PromptRegistry",
    "PromptRenderError",
    "PromptTemplate",
    "Provider",
    "ProviderError",
    "RateLimitError",
    "RateLimiterRegistry",
    "RetryPolicy",
    "SQLiteContextStore",
    "SQLiteUsageStore",
    "SearchHit",
    "Settings",
    "SyncRunner",
    "TokenBucket",
    "TokenUsage",
    "UsageRecord",
    "UsageSummary",
    "UsageTracker",
    "WarnetechError",
    "__version__",
    "build",
    "make_embed_fn",
]
