# Handoff — Cloudflare provider setup

Decided: own provider adapters (not LiteLLM); CLI + SQLite log shaped like simonw `llm`; first real provider = Cloudflare Workers AI; storage = R2 (objects) + D1 (memory); Supabase NOT for GPU (CPU only, per its compute docs). PRD draft: /tmp/claude-0/-home-user-warnetech-core/e0bb36d0-f77c-52ed-94cf-b95122460aa4/scratchpad/PRD.md (not in repo).

Stopped at: Cloudflare credentials. Earlier token in the old environment was rejected ("Invalid API Token"). The user has the Account ID, API token and R2 Secret Access Key, but they must be entered via request_credential in THIS environment. R2 Access Key ID was pasted in chat (not stored).

Next action: request_credential for api.cloudflare.com (bearer), then `curl -sS https://api.cloudflare.com/client/v4/user/tokens/verify` must return success:true. Then add src/warnetech_core/providers/workers_ai.py (Provider subclass, base.py) + tests/test_workers_ai.py with the account ID read from env var CLOUDFLARE_ACCOUNT_ID.

Blockers / notes:
- Tests: use /usr/bin/python3.11 (3.13 can't build pydantic-core).
- CLAUDE.md rule "one shared Supabase project, no new stores" conflicts with R2/D1; user approved R2/D1 in chat, rule not yet updated.
- docs/PRD.md is a different product (input-defense); unresolved.
- Docs wiki offer unanswered.

Open questions: none.
Uncommitted: none beyond this file.
**Autostart:** no
