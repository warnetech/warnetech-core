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
