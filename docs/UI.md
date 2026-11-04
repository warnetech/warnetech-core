# Local web UI

One page at `/ui`, served by the same service. It has a question box, a file upload that adds documents to memory, and an answer area with the sources it used.

```bash
pip install -e ".[service]"
WARNETECH_OFFLINE=1 WARNETECH_ALLOW_ANONYMOUS=1 \
  uvicorn warnetech_core.service.app:app --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080/ui>. Use `127.0.0.1`, not `0.0.0.0`, so only this machine can reach it. With `WARNETECH_API_TOKEN` set instead of `WARNETECH_ALLOW_ANONYMOUS=1`, the page shows a token box; paste the token (kept in the tab only).

- Uploads: text, Markdown, code, JSON, CSV, up to 1 MB each. Files that may hold secrets (`.env`, keys), hidden files and binaries are refused. Uploading a file with the same name replaces it.
- Safety: API routes refuse any `Host` or `Origin` that is not localhost (add names with `WARNETECH_UI_ALLOWED_HOSTS`). The page makes no outside requests and shows model and document text as plain text.
- Offline mode uses the echo provider, which repeats its prompt and has stub embeddings, so the "sources" it finds are not meaningful. Point `embed.default` and `chat.default` at real local models for real answers.
