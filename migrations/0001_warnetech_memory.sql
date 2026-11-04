-- migrations/0001_warnetech_memory.sql
--
-- Adds vector-backed memory storage for warnetech-core to the SHARED Warnetech
-- Supabase project, per the canonical wiring specification (decision 2: one
-- Supabase project, not a new one).
--
-- SAFETY: this migration is additive only. Every statement is CREATE ... IF NOT
-- EXISTS; there is no ALTER, no DROP, and no CREATE OR REPLACE. Running it
-- against a project that already has these objects is a verified no-op, and it
-- cannot modify a table it did not create. This mirrors the posture of
-- supabase_schema/tables.sql in claude-command-cli, which guards against
-- exactly this class of accident.
--
-- APPLY WITH CARE: `create extension vector` requires sufficient privileges and
-- is a project-wide change. Review before running against production.
--
-- Dimensionality is fixed per column in pgvector. 1536 matches
-- openai.text-embedding-3-small (the registry's `embed.default`). Changing
-- embedding models later means a new column or a new table, not an ALTER --
-- which is why the model alias is recorded on every row.

create extension if not exists vector;
create extension if not exists pgcrypto;

create table if not exists public.warnetech_memory (
  id            text not null,
  namespace     text not null,
  key           text,
  text          text not null,
  metadata      jsonb not null default '{}'::jsonb,
  embedding     vector(1536),
  -- Which registry alias produced `embedding`. Without this, a later model
  -- swap leaves rows whose vectors are silently incomparable.
  embed_model   text,
  created_at    double precision not null,
  updated_at    double precision not null,
  expires_at    double precision,
  primary key (namespace, id)
);

-- Upsert-by-key depends on this: MemoryService.remember(key=...) updates in
-- place rather than accumulating duplicates.
create unique index if not exists warnetech_memory_ns_key
  on public.warnetech_memory (namespace, key)
  where key is not null;

-- Listing a namespace, newest first.
create index if not exists warnetech_memory_ns_updated
  on public.warnetech_memory (namespace, updated_at desc);

-- TTL sweeps (MemoryService.purge_expired).
create index if not exists warnetech_memory_expires
  on public.warnetech_memory (expires_at)
  where expires_at is not null;

-- Cosine-distance ANN index, matching the `<=>` operator the store queries
-- with. Building this on an empty table is correct but lower quality than
-- reindexing once real data is present.
create index if not exists warnetech_memory_embedding_cos
  on public.warnetech_memory
  using hnsw (embedding vector_cosine_ops);

-- Row level security, consistent with the rest of the Warnetech schema.
-- No policies are created here: with RLS enabled and no policy, only the
-- service role can read or write, which is the correct default for a store
-- holding cross-project context. Add per-tenant policies deliberately.
alter table public.warnetech_memory enable row level security;
