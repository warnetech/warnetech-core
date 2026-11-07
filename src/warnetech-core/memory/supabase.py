"""Supabase-backed vector store.

Warnetech's canonical wiring specification (decision 2) makes the existing
Supabase project the single shared store — no new project, no parallel
database. This is the memory layer's production backend, and the reason the
local SQLite store is a development convenience rather than the destination.

Supabase is plain Postgres with the ``vector`` extension available, so this is
:class:`~warnetech_core.memory.pgvector.PgVectorContextStore` with two
differences that matter operationally:

* **It never issues DDL.** ``manage_schema`` defaults to False, because the
  shared project carries live data owned by other Warnetech components. Apply
  ``migrations/0001_warnetech_memory.sql`` deliberately instead — the same
  posture ``supabase_schema/migrations.py`` takes with its dry-run default.
* **It reads Supabase's own connection variable**, so a project already
  configured for Supabase needs no additional wiring.

Connection string: Supabase exposes it under Project Settings → Database. Use
the **pooled** (pgbouncer, port 6543) URI for short-lived processes and the
direct URI (port 5432) for long-running services.
"""

from __future__ import annotations

import os

from ..errors import ConfigError
from .pgvector import PgVectorContextStore

DEFAULT_TABLE = "warnetech_memory"


class SupabaseVectorStore(PgVectorContextStore):
    """Vector memory on the shared Supabase project."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        table: str = DEFAULT_TABLE,
        dimensions: int = 1536,
        manage_schema: bool = False,
        **kwargs: object,
    ) -> None:
        resolved = (
            dsn
            or os.getenv("WARNETECH_SUPABASE_DSN")
            or os.getenv("SUPABASE_DB_URL")
            or os.getenv("DATABASE_URL")
        )
        if not resolved:
            raise ConfigError(
                "No Supabase connection string. Set WARNETECH_SUPABASE_DSN (or "
                "SUPABASE_DB_URL / DATABASE_URL), or pass dsn= explicitly. Find it "
                "under Project Settings -> Database in the Supabase dashboard."
            )
        super().__init__(
            resolved,
            table=table,
            dimensions=dimensions,
            manage_schema=manage_schema,
            **kwargs,  # type: ignore[arg-type]
        )
