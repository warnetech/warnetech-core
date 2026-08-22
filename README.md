# warnetech-core

Shared **AI gateway** and **memory/context layer** for Warnetech projects.

Two problems, solved once instead of per project:

1. **AI Gateway** — every model call goes through one place that owns model
   abstraction, versioned prompts, cost tracking, retry, rate limiting and
   cross-provider fallback.
2. **Memory & context** — namespaced storage with a one-call retrieval API, so
   "does this AI remember X" is a dependency, not a rebuild.

Import it in-process, or run it as an HTTP service when several separately
deployed projects need one bill and one memory store. Same code path either way.

---

## Install

```bash
pip install -e .                    # library only
pip install -e ".[service]"         # + FastAPI HTTP service
pip install -e ".[pgvector]"        # + Postgres/pgvector store
pip install -e ".[dev]"             # + pytest, ruff
```

Python 3.10+.

## 60 seconds

Everything below runs with **no API keys** — `WARNETECH_OFFLINE=1` swaps in a
deterministic local provider so a project's full code path works before anyone
has provisioned anything.

```bash
cp .env.example .env
WARNETECH_OFFLINE=1 python examples/quickstart.py
```

```python
from warnetech_core import build

gateway, memory = build(project="hub-notes")

await memory.remember("hub-notes", "We chose Postgres for billing.", key="adr-1")

context = await memory.get_context("hub-notes", "which database for billing?")
reply = await gateway.complete("chat.default", f"{context.text}\n\nWhich DB?")

print(reply.text, reply.cost_usd)
```

---

## 1. AI Gateway

### Model abstraction

Downstream code names a **task**, never a provider model id:

```python
await gateway.complete("chat.default", "summarise this")
```

`chat.default` → `anthropic.claude-sonnet-4-5` → the Anthropic adapter. Changing
what every project uses is one line — in code, or in `models.yaml`:

```yaml
aliases:
  chat.default: openai.gpt-4o
```

`ModelRegistry` also holds pricing, context windows and fallback chains. See
`models.yaml.example`. Built-in task aliases: `chat.default`, `chat.fast`,
`chat.cheap`, `embed.default`, `embed.large`.

Adding a provider means implementing one method:

```python
class MyProvider(Provider):
    name = "mine"
    async def complete(self, request, spec) -> CompletionResponse: ...

gateway.register_provider(MyProvider())
```

Adapters translate one request and raise. Retry, limits, cost and logging stay
in the gateway, so they behave identically no matter who is upstream. Any
OpenAI-compatible endpoint (Together, Groq, local vLLM) works through
`OpenAIProvider(base_url=...)` with no new adapter.

### Prompt registry

Prompts are versioned data on disk, not literals in application code:

```yaml
# prompts/summarise.yaml
- id: summarise.document
  version: 2
  model: chat.fast
  messages:
    - role: system
      content: You are a precise summariser. Never invent facts.
    - role: user
      content: Summarise in at most {{ max_words }} words:\n\n{{ document }}
```

```python
response = await gateway.complete_prompt(
    "summarise.document",                # latest version
    {"document": text, "max_words": 100},
    project="hub-notes",
)
response = await gateway.complete_prompt("summarise.document", vars, version=1)  # pinned
```

- **Strict rendering.** A missing *or* unexpected variable raises at render
  time. A typo'd `{{ documnet }}` never reaches a model.
- **Immutable versions.** Re-registering a published `(id, version)` with
  different content is refused — bump the version, so old usage rows stay
  interpretable.
- **Traceable.** Each template has a content checksum, recorded on every call
  alongside its id and version.

### Cost tracking

Every call is attributed and recorded — successes *and* failures, because a call
that burned three retries and failed still cost you something.

```python
gateway.usage.summary(group_by="project")     # or team, model_alias, provider,
                                              # operation, prompt_id
gateway.usage.recent(project="hub-notes", limit=50)
```

```bash
warnetech usage --group-by project --hours 24
```

Optional trailing-window spend caps raise `BudgetExceeded` **before** dispatch:

```bash
WARNETECH_PROJECT_BUDGETS=hub-notes=5.00,hub-invoices=2.00
WARNETECH_TEAM_BUDGETS=core=25.00
```

The check runs against spend already recorded, so one call can overshoot the cap
— the alternative is predicting a response's token count before making it.

### Retry, rate limiting, fallback

One policy, inherited everywhere:

- **Retry** — exponential backoff with full jitter. A server's `Retry-After`
  always wins over our guess.
- **Rate limiting** — per-provider token buckets smooth bursts under a provider's
  RPM ceiling instead of discovering it via 429s.
- **Fallback** — when a model exhausts its retries, the gateway walks its
  fallback chain to another provider. The response records what it fell back
  from.

A 4xx (except 408/409/429) means *our request* was malformed — the gateway fails
fast rather than replaying a broken payload down the whole chain.

```python
AIGateway(providers, retry=RetryPolicy(max_attempts=5), enable_fallback=True)
```

---

## 2. Memory & context layer

### Per-project namespacing

The namespace is `project:scope`. **Isolation is the default and crossing it is
explicit** — a hub app writing to `notes` cannot surface a record from
`invoices` unless the caller names it:

```python
await memory.recall("hub-notes", "acme invoice")                          # notes only
await memory.recall("hub-notes", "acme invoice", also_projects=["hub-invoices"])
```

`scope` sub-partitions within a project — a user id, a session, a collection:

```python
await memory.remember("hub", "Alice prefers tea.", scope="user-a")
await memory.recall("hub", "drink preference", scope="user-a")
await memory.recall("hub", "drink preference", scopes=["user-a", "user-b"])
```

### The retrieval API

```python
result = await memory.get_context("hub-notes", "what did we decide about X?")
result.text     # formatted, citable block, packed to a token budget
result.hits     # the records behind it, best first
```

`get_context` packs hits best-first until `max_tokens`; a record too large for
the remaining budget is skipped rather than cut mid-sentence, and a smaller one
behind it may still fit. `result.truncated` tells you when that happened.

Storing:

```python
await memory.remember("hub-notes", "We chose Postgres.", key="adr-1",
                      metadata={"kind": "decision"})
await memory.remember_many("hub-notes", ["one", "two", "three"])   # one embed call
await memory.remember("hub", "session state", key="s", ttl_seconds=3600)
await memory.remember("hub", "opaque blob", key="k", embed=False)  # skips the embed cost
```

Writing the same `key` **updates in place** — id and creation time survive, so
references held elsewhere stay valid. Records with a TTL disappear from every
read the moment they expire; `purge_expired()` reclaims the storage.

Filtering:

```python
await memory.recall("hub", q, metadata_filter={"kind": "decision"})
await memory.recall("hub", q, metadata_filter={"kind": ["decision", "note"]})  # any of
await memory.recall("hub", q, min_score=0.3)   # keep weak matches out of the prompt
```

### Pluggable storage

| Backend | Use for | Setup |
|---|---|---|
| `InMemoryContextStore` | tests, short scripts | none |
| `SQLiteContextStore` | **default** — real projects | none, a file |
| `PgVectorContextStore` | production scale | Postgres + `vector` |

```bash
WARNETECH_MEMORY_BACKEND=sqlite        # memory | sqlite | pgvector
WARNETECH_MEMORY_PATH=./data/warnetech_memory.db
```

SQLite stores vectors as float32 blobs and scores cosine similarity in Python:
brute force over tens of thousands of records is milliseconds, with zero extra
dependencies. Past that, switch to pgvector — the interface is identical, so
nothing downstream changes.

Adding a backend means implementing `ContextStore`. Stores handle persistence
and filtering only; embedding, namespacing and context assembly stay in
`MemoryService` so every backend behaves the same. The shared test suite runs
against each store, so conformance is proven rather than assumed.

---

## HTTP service (optional)

For projects that live in the same process, import the gateway directly — same
code path, no network hop. Run the service when separately deployed projects
need one bill and one memory store:

```bash
pip install -e ".[service]"
export WARNETECH_API_TOKEN=$(openssl rand -hex 32)
uvicorn warnetech_core.service.app:app --port 8080
```

**The service refuses to start without `WARNETECH_API_TOKEN`** rather than
defaulting to open — a gateway holding every provider key is not something to
leave unauthenticated. Set `WARNETECH_ALLOW_ANONYMOUS=1` to opt out deliberately
for local work.

| Method | Path | |
|---|---|---|
| GET | `/health` | public liveness |
| GET | `/models` | registry, aliases, availability |
| GET | `/prompts`, `/prompts/{id}` | templates and versions |
| POST | `/v1/complete` | chat completion |
| POST | `/v1/complete/prompt` | render a template and run it |
| POST | `/v1/embed` | batch embeddings |
| GET | `/v1/usage/summary`, `/v1/usage/recent` | cost reporting |
| POST | `/v1/memory/remember` | store |
| POST | `/v1/memory/recall` | semantic search |
| POST | `/v1/memory/context` | retrieve + assemble |
| GET/DELETE | `/v1/memory/{project}` | list / forget |

Errors map to status codes: `402` over budget, `404` unknown model or prompt,
`400` bad prompt variables, `502` every provider failed. Interactive docs at
`/docs`.

---

## CLI

```bash
warnetech models                          # what does chat.default actually hit?
warnetech prompts                         # templates, versions, checksums
warnetech prompts summarise.document --version 2
warnetech usage --group-by team --hours 24
warnetech remember "Standup is at 09:30." --project hub-notes
warnetech ask "when is standup?" --project hub-notes --with-memory
```

## Configuration

Everything is environment-driven; see `.env.example` for the full list.
`build()` reads it and returns a wired gateway and memory service. Anything it
does can also be done by hand when a project wants something unusual:

```python
gateway = AIGateway(
    {"anthropic": AnthropicProvider()},
    registry=my_registry,
    usage=UsageTracker(SQLiteUsageStore("costs.db"), project_budgets_usd={"hub": 5.0}),
    retry=RetryPolicy(max_attempts=5),
)
memory = MemoryService(gateway, SQLiteContextStore("memory.db"))
```

## Development

```bash
pip install -e ".[dev,service]"
pytest              # 186 tests, fully offline
ruff check src tests
```

The suite needs no API keys and makes no network calls: provider adapters are
driven through a mocked transport, and everything else runs against the
deterministic `echo` provider. Memory tests are parametrised across both
built-in stores.

### Layout

```
src/warnetech_core/
  gateway.py      AIGateway — dispatch, retry, fallback, accounting
  registry.py     ModelSpec / ModelRegistry — aliases, pricing, fallbacks
  usage.py        UsageRecord, stores, UsageTracker, budgets
  resilience.py   RetryPolicy, TokenBucket, rate limiter registry
  config.py       Settings.from_env(), build()
  cli.py          `warnetech` command
  providers/      base, http, anthropic, openai, echo (offline)
  prompts/        PromptTemplate (versioned, strict), PromptRegistry
  memory/         types, base, inmemory, sqlite, pgvector, service
  service/        FastAPI app + schemas
```

## Status

`0.1.0`. The pgvector store is written against psycopg3 and pgvector but has not
yet been exercised against a live Postgres — the in-memory and SQLite stores are
covered by the shared conformance suite. Model prices in the built-in catalogue
drift; override them in `models.yaml` rather than trusting the table.
