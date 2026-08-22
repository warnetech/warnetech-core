# Alignment with the Warnetech system

`warnetech-core` is built to drop into the existing Warnetech architecture
defined in [`claude-command-cli`](https://github.com/tewartech-node/claude-command-cli),
not to sit beside it. This records what it inherits from that repo's binding
decisions and where it plugs in.

## Canonical wiring decisions honoured

From `docs/WARNETECH-CANONICAL-WIRING-SPEC.txt` (v1.0, Canonical Decision Record):

### Decision 1 — in-process, not HTTP

> *warnetech_server/control_plane_client.py must call warnetech_control_plane
> in-process using direct Python imports. No HTTP layer should be created…
> for maximum speed, security, and simplicity.*

`AIGateway` is a **library, imported in-process**. That is the supported path
and the one every example uses; `warnetech_server` should import it the same
way `ai_integration.py` imports `warnetech_ai_controller` — no network hop, one
shared process.

The FastAPI app under `service/` is **not** part of that path. It exists only
for projects deployed separately from `warnetech-server` that still need one
bill and one memory store. It wraps the gateway, never the control plane, so it
does not create the HTTP layer decision 1 rules out. If everything runs in one
process, do not deploy it.

### Decision 2 — one shared Supabase project

> *The Supabase project backing this repository must be the same Supabase
> project used in the earlier Warnetech work. No new Supabase project should
> be created.*

`SupabaseVectorStore` is the production memory backend, pointed at the existing
project. The SQLite store is a development convenience, not a destination.

Because that project holds live data owned by other components, the store
**never issues DDL**: `manage_schema` defaults to `False`, and a missing table
raises with a pointer to the migration rather than silently creating one. This
mirrors the posture `supabase_schema/migrations.py` takes with its `dry_run`
default and its `SAFE_RPC_FUNCTIONS` allowlist.

`migrations/0001_warnetech_memory.sql` is additive only — `CREATE … IF NOT
EXISTS` throughout, no `ALTER` beyond enabling RLS, no `DROP`, no `CREATE OR
REPLACE`. A test asserts this, so the property cannot regress.

## Where it plugs in

### The `embed_fn` seam

`warnetech_ai_controller/embeddings.py` accepts an `embed_fn` on every
function, documented as the injection point for "genuine semantic embeddings".
Its default is a SHA-256 projection of the whole serialised payload — described
there as a "deterministic, dependency-free fallback" — which supports only
exact and near-duplicate recall, since two payloads differing by one byte hash
to unrelated vectors.

`warnetech_core.integrations.make_embed_fn()` returns a drop-in replacement
backed by a real provider, with retry, fallback and per-project cost
attribution attached:

```python
from warnetech_core import AIGateway
from warnetech_core.integrations import make_embed_fn
from warnetech_ai_controller.embeddings import generate_slice_embedding
from warnetech_ai_controller.relevance import rank_by_similarity

gateway = AIGateway.from_env()
embed = make_embed_fn(gateway, project="warnetech-firewall")

vector = generate_slice_embedding(slice_metadata, embed_fn=embed)
ranked = rank_by_similarity(embed(query_text), slice_embeddings)
```

Nothing in the AI controller changes.

**Dimensionality.** `AIControllerConfig.EmbeddingSettings` defaults to 768;
`embed.default` (openai.text-embedding-3-small) produces 1536. Cosine
similarity needs equal dimensions, so a corpus must be embedded consistently.
Pass a matching `EmbeddingSettings(dimensions=...)` when mixing, and re-embed
rather than comparing across models — every `MemoryRecord` records the
`embed_model` that produced it, and searches filter on it, so a model swap
hides stale vectors instead of scoring them into confident nonsense.

**Sync/async.** The AI controller is synchronous; the gateway is async because
it does network I/O with retries. `warnetech_core.sync.SyncRunner` bridges
them on one long-lived loop thread — not `asyncio.run` per call, which would
fail inside an existing loop and rebuild the `httpx` connection pool every time.

### Nemotron

`worker/utils/nemotron.js` calls NVIDIA's endpoint directly with a hardcoded
model and system prompt, no retry, no fallback, and token counts that are
returned but never accumulated. That worker is legacy — `CLAUDE.md`: *"No new
features go into `worker/`. Add them to `warnetech_server/`."*

The same model is registered here as `chat.nemotron`
(`nvidia/nemotron-3-ultra`), with a fallback chain to Claude and GPT. Routing
through the gateway is what gives that call prompt versioning, retry, fallback
and cost attribution. NVIDIA's API is OpenAI-compatible, so the adapter is a
configuration of `OpenAIProvider` — but registered under its own provider name
so a Nemotron burst cannot spend OpenAI's rate-limit or cost budget.

## What the gateway adds that did not exist

There is no real model or embedding call anywhere on the canonical Python path.
`AIIntegration.generate_embedding()` returns a hash;
`EmbeddingSettings.model` is literally `"hash-projection"`. The only genuine
LLM call in the system is in the frozen legacy worker.

So this is not a replacement for `warnetech_ai_controller` — that layer owns
slice relevance, recall planning and reconstruction, and keeps doing so. This
supplies the provider access underneath it, plus the cost and reliability
controls that access needs.
