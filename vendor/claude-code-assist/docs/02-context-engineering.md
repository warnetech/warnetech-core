# Context engineering

Retrieval quality dominates almost every other knob. A weaker model with the
right three files beats a stronger model with the wrong thirty, and costs a
tenth as much.

## walk → rank → pack

```python
from llmforge import walk_repo, rank, pack, build_context

packed = build_context("the uploader retries twice on 503", root=".",
                       budget_tokens=60_000, pinned=["src/upload/client.py"])
```

`build_context` is the 90% path. Use the three stages directly when you need to
inspect or override the middle.

## Ranking signals, in order of how much they matter

1. **Pinned paths** — always first. The human knows something the ranker does not.
2. **Query terms in the path** — strong and nearly free.
3. **Query terms in the body** — saturating, so one file cannot win by repeating
   a word two hundred times.
4. **Recently edited** — proximity to the current task. Feed it `git status`.
5. **Anchor files** — `README`, `CLAUDE.md`, `pyproject.toml`. They orient a
   reader who has never seen the repo.

Every chunk carries a `reasons` list explaining its score. A retrieval step you
cannot explain is one you cannot debug when it starts returning the wrong files.

## Two packing rules that earn their keep

**Never truncate silently.** A cut file carries an explicit
`... N lines elided ...` marker, and the packed output ends with an
`<elided-files>` block naming everything that did not fit. A model that knows
it is missing code asks for it; a model that does not confidently reasons about
code it never saw.

**Skip rather than shrink to nothing.** Below `min_chunk_tokens` a fragment is
noise. The budget is better spent finishing the next file.

## The repo map belongs in the stable prefix

```python
from llmforge import repo_map, walk_repo

MAP = repo_map(walk_repo("."), root=".")   # paths plus top-level declarations
```

It is small, it changes rarely, and it lets the model ask for the right file by
name instead of guessing. Putting it in the cached prefix is what actually
collapses retrieval cost on a long session.

## Caching is a prefix match

Any byte change anywhere in the prefix invalidates everything after it. Render
order is `tools` → `system` → `messages`.

Silent invalidators, in rough order of how often they bite:

| Invalidator | Fix |
|---|---|
| `datetime.now()` or a UUID in the system prompt | move it after the last breakpoint |
| unsorted `json.dumps` | `sort_keys=True` |
| tool list reordered between calls | `ToolRegistry` preserves insertion order — keep it |
| editing the system prompt mid-session | append a `{"role": "system"}` message instead |
| switching models mid-session | keep the main loop on one model; use a subagent for the cheap sub-task |

Verify with `usage.cache_read_input_tokens`. A persistent zero across repeated
identical-prefix requests means one of the above is at work.

## Token counting

`estimate_tokens` is chars/4 — fine for budgeting, wrong enough that you should
not bill on it. When accuracy matters, pass a counter backed by
`client.messages.count_tokens`. Never `tiktoken`: it models a different
tokenizer entirely and will be confidently wrong.
