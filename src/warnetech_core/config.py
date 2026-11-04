"""Environment-driven wiring.

``build()`` is the one call a mini-project needs: it reads the environment,
picks providers and a store, and hands back a ready gateway plus memory
service. Everything it does can also be done by hand when a project wants
something unusual.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gateway import AIGateway
from .memory.base import ContextStore
from .memory.inmemory import InMemoryContextStore
from .memory.service import MemoryService
from .memory.sqlite import SQLiteContextStore
from .prompts import PromptRegistry
from .registry import ModelRegistry
from .resilience import RateLimiterRegistry, RetryPolicy
from .usage import InMemoryUsageStore, SQLiteUsageStore, UsageTracker


def _env_float(name: str, default: float | None = None) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _parse_budgets(raw: str | None) -> dict[str, float]:
    """Parse ``"proj-a=5.00,proj-b=1.50"`` into a budget map."""
    if not raw:
        return {}
    budgets = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, value = chunk.partition("=")
        if not value:
            raise ValueError(f"budget entry {chunk!r} must look like name=amount")
        budgets[name.strip()] = float(value)
    return budgets


@dataclass
class Settings:
    """Resolved configuration. See :meth:`from_env` for the variable names."""

    project: str = "default"
    team: str | None = None
    offline: bool = False

    prompts_dir: str | None = None
    models_file: str | None = None
    # Local Ollama catalogue (config/local_models.yaml). Loaded after models_file.
    local_models_file: str | None = None
    # Directory of custom model specs (config/custom_models). Each becomes an alias.
    custom_models_dir: str | None = None
    # Refuse every hosted provider: nothing leaves the machine, no tokens spent.
    local_only: bool = False

    memory_backend: str = "sqlite"  # sqlite | memory | pgvector | supabase
    memory_path: str = "./data/warnetech_memory.db"
    postgres_dsn: str | None = None
    supabase_dsn: str | None = None
    # Supabase is a shared live project, so the store never issues DDL there by
    # default; apply migrations/0001_warnetech_memory.sql deliberately instead.
    supabase_manage_schema: bool = False
    pgvector_table: str = "warnetech_memory"
    embed_model: str = "embed.default"
    embed_dimensions: int = 1536

    usage_backend: str = "sqlite"  # sqlite | memory
    usage_path: str = "./data/warnetech_usage.db"
    project_budgets_usd: dict[str, float] = field(default_factory=dict)
    team_budgets_usd: dict[str, float] = field(default_factory=dict)
    budget_window_seconds: float = 86_400.0

    retry_max_attempts: int = 3
    enable_fallback: bool = True
    provider_rate_limit_per_sec: float | None = None

    @classmethod
    def from_env(cls, **overrides: Any) -> Settings:
        """Read settings from ``WARNETECH_*`` environment variables.

        Recognised variables (all optional):
        ``WARNETECH_PROJECT``, ``WARNETECH_TEAM``, ``WARNETECH_OFFLINE``,
        ``WARNETECH_PROMPTS_DIR``, ``WARNETECH_MODELS_FILE``,
        ``WARNETECH_MEMORY_BACKEND``, ``WARNETECH_MEMORY_PATH``,
        ``WARNETECH_POSTGRES_DSN``, ``WARNETECH_PGVECTOR_TABLE``,
        ``WARNETECH_EMBED_MODEL``, ``WARNETECH_EMBED_DIMENSIONS``,
        ``WARNETECH_USAGE_BACKEND``, ``WARNETECH_USAGE_PATH``,
        ``WARNETECH_PROJECT_BUDGETS``, ``WARNETECH_TEAM_BUDGETS``,
        ``WARNETECH_BUDGET_WINDOW_SECONDS``, ``WARNETECH_RETRY_MAX_ATTEMPTS``,
        ``WARNETECH_ENABLE_FALLBACK``, ``WARNETECH_RATE_LIMIT_PER_SEC``.
        """
        settings = cls(
            project=os.getenv("WARNETECH_PROJECT", "default"),
            team=os.getenv("WARNETECH_TEAM") or None,
            offline=_env_bool("WARNETECH_OFFLINE", False),
            prompts_dir=os.getenv("WARNETECH_PROMPTS_DIR") or None,
            models_file=os.getenv("WARNETECH_MODELS_FILE") or None,
            local_models_file=os.getenv("WARNETECH_LOCAL_MODELS") or None,
            custom_models_dir=os.getenv("WARNETECH_CUSTOM_MODELS_DIR") or None,
            local_only=_env_bool("WARNETECH_LOCAL_ONLY", False),
            memory_backend=os.getenv("WARNETECH_MEMORY_BACKEND", "sqlite"),
            memory_path=os.getenv("WARNETECH_MEMORY_PATH", "./data/warnetech_memory.db"),
            postgres_dsn=os.getenv("WARNETECH_POSTGRES_DSN") or None,
            supabase_dsn=(
                os.getenv("WARNETECH_SUPABASE_DSN")
                or os.getenv("SUPABASE_DB_URL")
                or os.getenv("DATABASE_URL")
                or None
            ),
            supabase_manage_schema=_env_bool("WARNETECH_SUPABASE_MANAGE_SCHEMA", False),
            pgvector_table=os.getenv("WARNETECH_PGVECTOR_TABLE", "warnetech_memory"),
            embed_model=os.getenv("WARNETECH_EMBED_MODEL", "embed.default"),
            embed_dimensions=int(os.getenv("WARNETECH_EMBED_DIMENSIONS", "1536")),
            usage_backend=os.getenv("WARNETECH_USAGE_BACKEND", "sqlite"),
            usage_path=os.getenv("WARNETECH_USAGE_PATH", "./data/warnetech_usage.db"),
            project_budgets_usd=_parse_budgets(os.getenv("WARNETECH_PROJECT_BUDGETS")),
            team_budgets_usd=_parse_budgets(os.getenv("WARNETECH_TEAM_BUDGETS")),
            budget_window_seconds=(
                _env_float("WARNETECH_BUDGET_WINDOW_SECONDS", 86_400.0) or 86_400.0
            ),
            retry_max_attempts=int(os.getenv("WARNETECH_RETRY_MAX_ATTEMPTS", "3")),
            enable_fallback=_env_bool("WARNETECH_ENABLE_FALLBACK", True),
            provider_rate_limit_per_sec=_env_float("WARNETECH_RATE_LIMIT_PER_SEC"),
        )
        for key, value in overrides.items():
            setattr(settings, key, value)
        return settings

    # ---- component builders --------------------------------------------

    def build_usage(self) -> UsageTracker:
        if self.usage_backend == "memory":
            store: Any = InMemoryUsageStore()
        elif self.usage_backend == "sqlite":
            store = SQLiteUsageStore(self.usage_path)
        else:
            raise ValueError(f"unknown usage backend {self.usage_backend!r}")
        return UsageTracker(
            store,
            project_budgets_usd=self.project_budgets_usd,
            team_budgets_usd=self.team_budgets_usd,
            budget_window_seconds=self.budget_window_seconds,
        )

    def build_store(self) -> ContextStore:
        if self.memory_backend == "memory":
            return InMemoryContextStore()
        if self.memory_backend == "sqlite":
            return SQLiteContextStore(self.memory_path)
        if self.memory_backend == "supabase":
            from .memory.supabase import SupabaseVectorStore

            return SupabaseVectorStore(
                self.supabase_dsn,
                table=self.pgvector_table,
                dimensions=self.embed_dimensions,
                manage_schema=self.supabase_manage_schema,
            )
        if self.memory_backend == "pgvector":
            if not self.postgres_dsn:
                raise ValueError("memory backend 'pgvector' needs WARNETECH_POSTGRES_DSN")
            from .memory.pgvector import PgVectorContextStore

            return PgVectorContextStore(
                self.postgres_dsn,
                table=self.pgvector_table,
                dimensions=self.embed_dimensions,
            )
        raise ValueError(f"unknown memory backend {self.memory_backend!r}")

    def build_registry(self) -> ModelRegistry:
        registry = ModelRegistry.default()
        if self.offline:
            registry.use_offline_models()
        if self.models_file:
            # Explicit overrides win over the offline defaults.
            registry.load_yaml(self.models_file)
        if self.local_models_file:
            registry.load_yaml(self.local_models_file)
        if self.custom_models_dir:
            from .custom import register_custom_models

            register_custom_models(registry, self.custom_models_dir)
        return registry

    def build_prompts(self) -> PromptRegistry:
        registry = PromptRegistry()
        if self.prompts_dir and Path(self.prompts_dir).is_dir():
            registry.load_dir(self.prompts_dir)
        return registry

    def build_gateway(self) -> AIGateway:
        limiters = RateLimiterRegistry(self.provider_rate_limit_per_sec)
        kwargs: dict[str, Any] = {
            "registry": self.build_registry(),
            "prompts": self.build_prompts(),
            "usage": self.build_usage(),
            "retry": RetryPolicy(max_attempts=self.retry_max_attempts),
            "limiters": limiters,
            "default_project": self.project,
            "enable_fallback": self.enable_fallback,
        }
        if self.offline:
            return AIGateway.offline(**kwargs)
        return AIGateway.from_env(local_only=self.local_only, **kwargs)

    def build_memory(self, gateway: AIGateway) -> MemoryService:
        return MemoryService(gateway, self.build_store(), embed_model=self.embed_model)


def build(**overrides: Any) -> tuple[AIGateway, MemoryService]:
    """Build a configured gateway and memory service from the environment.

    Example:
        >>> gateway, memory = build(project="hub-notes")   # doctest: +SKIP
    """
    settings = Settings.from_env(**overrides)
    gateway = settings.build_gateway()
    return gateway, settings.build_memory(gateway)
