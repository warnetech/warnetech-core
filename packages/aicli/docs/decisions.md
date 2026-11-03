# Decisions: where Phase 1 departs from the PRD

The PRD's stack table proposed Typer + Rich + Pydantic + YAML + LiteLLM + httpx.
Phase 1 ships on the **standard library only**. Each departure can be reversed
once a later phase needs what the dependency adds.

| PRD | Phase 1 | Why |
|---|---|---|
| LiteLLM | Three small HTTP providers (`providers.py`) | The PRD's own risk table says to "abstract behind our own provider interface". Phase 1 needs three providers, not 100. LiteLLM can come back later as one more provider type behind the same interface. |
| Typer + Rich | argparse | No dependencies, and the install is instant. Rich output is a cosmetic Phase 1.5 item. |
| Pydantic + YAML | dataclasses + TOML (`tomllib`) | TOML parsing is in the stdlib from Python 3.11. Validation errors still name the bad field. |
| httpx (async) | urllib (sync) | One call at a time needs no async. Batch (Phase 2) is the point to add httpx. |
| `aicli config --set anthropic.key $KEY` | `api_key_env = "ANTHROPIC_API_KEY"` | A key written into a config file eventually gets pasted into an issue. The config stores the variable's name, not the secret. |
| Default model `claude-sonnet` | Default `local/llama3.2`, `fallback = []` | The PRD's first value is "privacy by default / local-first". Falling back from local to cloud would send the prompt off the machine, so the user has to opt in by listing a cloud model in `fallback`. |

Other choices:

- **Unknown prices report `n/a`, not `$0`.** A confident zero in a cost report is
  worse than an honest unknown. Prices carry a `PRICES_AS_OF` date. Override them in `[prices]`.
- **Failed attempts are stored in history** (`status = 'error'`), so every
  fallback can be audited later.
- **Anthropic `stop_reason: "refusal"` is an error.** It comes back as HTTP 200
  with no text, and printing an empty string would hide it.

## Added with the gap-list features (v0.2)

- **A project `.aicli.toml` needs trust, pinned to its hash.** A project config
  can start processes (MCP servers) and point a provider's `base_url`
  elsewhere, which would send your API key there. So a freshly cloned repo's
  config is ignored, with a notice, until `aicli project trust`, and any edit
  needs trusting again. Templates are only prompt text and load without trust.
- **MCP tools are denied by default.** Only names in a server's `allow` list
  are offered to the model. A call to a tool it was never offered is refused,
  not attempted.
- **No fallback after a tool has run.** If the model fails mid-loop after a
  tool executed, aicli stops (exit 1) instead of retrying on the next model,
  because replaying side-effecting tools elsewhere is unsafe.
- **The audit log holds hashes, not prompts.** It is meant to be shareable with
  a security reviewer. The history database holds the content.
- **Routing quality is user-assigned.** A measured score needs an eval harness;
  until then the 1–5 scale in `[routing.quality]` is honest about being
  your judgement.
- **Budgets use the worst case across the whole fallback chain**, so a
  fallback to a pricier model cannot slip past the check.
- **Comparisons run in threads; SQLite writes stay on the main thread.**
  Provider calls are I/O-bound, so threads give the speed-up without
  needing a second SQLite connection.
