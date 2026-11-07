# Your own memory

Everything is stored in your SQLite file (`WARNETECH_MEMORY_PATH`), on your machine.

| Layer | What it holds | How it gets in |
|---|---|---|
| Core memory | Facts always sent with every chat turn | `warnetech memory core-add "..."`, or `/core ...` in chat |
| Documents (RAG) | Chunks of your files, found by similarity | `warnetech memory ingest <file-or-folder>` |
| Conversation | Past chat turns, found by similarity | Saved automatically; `warnetech chat --no-save` disables it |

```bash
warnetech memory ingest ./notes          # re-run any time: updates in place
warnetech memory core-add "My name is Ada"
warnetech memory core-list
warnetech chat                           # /core, /remember, /exit
```

- Ingest skips hidden files, anything that may hold secrets (`.env`, keys), binaries, files over 1 MB and symlinks.
- Retrieved text is fenced as data and the model is told not to obey it. That reduces prompt injection; it does not eliminate it.
- Search is exact and in Python: fine for thousands of chunks, slow beyond that. `sqlite-vec` is not used yet.
- Embeddings come from the `embed.default` alias. Offline it is a test stub, so similarity is not meaningful until you point that alias at a real local embedding model (for example one served by Ollama).

## Local models

`OLLAMA_HOST`, `LMSTUDIO_HOST`, `LLAMACPP_HOST` (llama-server) or `WARNETECH_HF_MODELS_DIR` each register a provider. Set `WARNETECH_LOCAL_ONLY=1` to register no hosted provider.

## Sub-agents

In `warnetech chat`: `/agents` lists them, `/delegate <agent> <task>` runs one. Definitions live in `config/agents/*.yaml` (`researcher`, `coder`, `reviewer`); add your own.

Limits enforced in code: no nesting, 3 running at once, 8 jobs per call, a 20,000-token budget per call (jobs not yet started are skipped once it is used), and memory is read-only and opt-in per agent (`memory: true`). An agent returns text only; nothing it returns is run or saved.

Delegation is manual: the chat model does not decide to delegate on its own yet, because small local models are unreliable at it.

## Flat-file memory (`warnetech_memory`)

A second, separate store: one append-only file, `data/rag_store.jsonl`, standard library only, keyword (BM25) search. It does not use embeddings or the SQLite store above.

```bash
warnetech_memory add "The vault code is blue." --source notes.md
warnetech_memory search vault code
warnetech_memory context "what is the vault code?"   # text with sources, ready for a prompt
warnetech_memory list
warnetech_memory delete <id>
```

- The same text added twice (ignoring case and spacing) is stored once; a second source is recorded against it.
- Deleting writes a tombstone. The file is never edited, so a deleted note is still in it.
- Unreadable lines are skipped and counted on stderr, never fatal. Exit codes: 0 ok, 1 nothing found, 2 bad input.
- Path: `--store`, else `WARNETECH_RAG_STORE_PATH`, else `data/rag_store.jsonl` under the nearest folder with `pyproject.toml`.
- Earlier prototype files (`created_at` and `text` only) still load.
- Measured here: loading 10,000 records and searching them took 0.21 s. Search reads the whole file each time, so a very large store will be slower.
