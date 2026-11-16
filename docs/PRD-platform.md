# warnetech-core platform PRD (gateway, CLI, memory, agents)

Status: draft for review, 2026-11-15. Nothing in the codebase was changed to produce it.

This sits beside `docs/PRD.md` (the air-gapped defense-engine job catalogue) and does not replace it.
The two describe different products; see Decision D1.

## 1. Summary

warnetech-core already contains most of what a first release needs: a provider-neutral gateway with
retry, fallback and cost accounting, a `warnetech` CLI (`ask`, `chat`, `remember`, `models`,
`prompts`, `usage`, `doctor`), four memory backends, an MCP server, routing, and agent modules.

The problem is not missing features. It is that **the package cannot currently be installed**, so
none of that reaches a user. The first phase is therefore a repair-and-ship phase, not a build phase.
Later phases harden what exists and adopt outside projects only at the edges, as optional extras.

## 2. Verified baseline (this session)

| Check | Result |
|---|---|
| `pip install -e ".[dev]"`, Python 3.13 | **Fails.** `pydantic>=2.0,<2.6` resolves to pydantic-core 2.14, which has no 3.13 wheel and fails to build. |
| `pip install -e ".[dev]"`, Python 3.12 | **Fails.** `Invalid script entry point`: `[project.scripts]` points at `warnetech-core.cli:main`; a hyphen is not a valid module name. Should be `warnetech_core.cli:main` (same for `warnetech_memory`). |
| `warnetech` command | Not created, because of the above. |
| `python -m warnetech_core.cli ask "test"` (offline, 3.12) | Works; answers through the echo provider. |
| `pytest` (3.12) | 796 passed, 2 skipped. 3 files fail at collection (`test_service`, `test_warnetech_ui`, `test_webhooks`): they import `fastapi`, which is in the `service` extra but not `dev`. |
| `ruff check src tests` | Clean. |
| Codeflash | `CODEFLASH_API_KEY` is set and `[tool.codeflash]` is configured. The CLI is not installed in this container and no workflow references it. |

Not verified: Windows 7, Termux, and Python 3.8 to 3.10 behaviour (no such interpreters here).

Inconsistencies found:

- `pyproject.toml` says `requires-python >=3.8`; `CLAUDE.md` says 3.10+; CI tests 3.10 to 3.12 only. Nothing tests 3.8.
- `CLAUDE.md` says "zero required runtime dependencies"; `pyproject.toml` requires httpx, pydantic and pyyaml.
- The previous session's notes recommended Click/Typer and Chroma. Both conflict with the dependency
  rule, and both duplicate things that already exist (an argparse CLI with a command-group framework
  in `cli_core.py`; SQLite, pgvector, Supabase and JSONL+BM25 memory). This PRD recommends against both.

## 3. Landscape: what exists, and what to do about it

"Checked" means read from a current source this session. "Recalled" means from prior knowledge and
should be re-checked before any adoption work starts.

| Area | Existing work | Basis | Recommendation |
|---|---|---|---|
| Multi-provider gateway (library) | LiteLLM (100+ providers, SDK plus proxy, MIT; heavy install including boto3, tokenizers, aiohttp). any-llm (Mozilla, Python 3.11+, wraps official SDKs). aisuite (smallest install, Python 3.10+). | Checked, via a vendor comparison that is openly biased toward its own product | **Keep our gateway.** All three need Python 3.10 or newer and pull many packages, which rules them out for the low-end targets. Optionally add LiteLLM as one more *provider* behind an extra, to borrow its provider coverage without depending on it. |
| Gateway as a separate server | LiteLLM Proxy, Bifrost (Go), LLM Gateway (AGPL), OpenRouter (hosted) | Checked | **Do not build.** Rule 1 says in-process. If a shared proxy is ever needed, point our OpenAI-compatible provider at one of these. |
| Terminal LLM tool | `llm` by Simon Willison (plugins, SQLite log of every call, templates) | Recalled | **Borrow ideas, not code:** stdin piping, `-s` system prompt, `--continue`, a logs command. Closest prior art to our CLI. |
| CLI framework | Click, Typer | Recalled | **Do not adopt.** argparse plus `cli_core.py` already works and costs no dependency. |
| Agent memory | Mem0 (extract facts, reconcile), Letta (agent edits tiered memory), Graphiti/Zep (temporal graph, needs a graph database). Vendor benchmark scores disagree widely with the one independent run (Mem0 94.4 self-reported vs 49.0 independent on LongMemEval). | Checked | **Keep our stores.** Borrow two patterns: Mem0's add/update/skip reconciliation on write, and Letta's always-in-context "core memory" block (`chat.py` already has one). Do not trust published benchmark numbers; measure on our own data. |
| Vector search | Chroma, sqlite-vec, pgvector | Recalled | pgvector and SQLite already present. **Skip Chroma.** sqlite-vec is the only candidate worth a later look, since it adds vectors to the SQLite file we already write. |
| Tool protocol | Model Context Protocol; official Python SDK | Recalled | We ship a hand-written stdio server in `mcp-servers/warnetech_mcp`. Phase 3: test it against real clients, and add an MCP *client* so agents can use outside servers. |
| Agent frameworks | LangGraph, CrewAI, PydanticAI, OpenAI Agents SDK | Recalled | **Do not adopt.** `agi/` and `delegation/` cover this; a framework would bypass the gateway (Rule 7). |
| Performance optimiser | Codeflash: rewrites a function, checks behaviour is unchanged with tests, benchmarks it, opens a pull request. CLI (`codeflash --all`, `codeflash optimize file.py`) and a GitHub Action for new PRs. | Checked (project README and docs) | **Use as a dev tool only, never bundled** (as already agreed). See 5.3. |

Honest positioning: no single project combines an in-process gateway, a CLI, memory and agents with
a tiny dependency set that runs on old or constrained machines. That gap is real, but each piece
has a stronger specialist. The defensible niche is "small, offline-capable, runs anywhere", so
install reliability on those targets is the product, and Phase 1 exists to prove it.

## 4. Goals and non-goals

Goals: one `pip install` that works on every supported interpreter; a CLI useful with no API key;
memory that persists locally by default; costs visible per call.

Non-goals for now: a hosted service, a web UI beyond the existing local one, a plugin marketplace,
new providers, multi-user auth, and any new heavy dependency in the base install.

## 5. Phases

### Phase 1: installable and trustworthy (the quick win)

No new features. Done when a clean machine can install and run it.

| ID | Work | Accept when |
|---|---|---|
| P1-1 | Fix `[project.scripts]` to `warnetech_core.cli:main` and `warnetech_core.memory.warnetech_memory:main` | `pip install .` succeeds; `warnetech --help` and `warnetech_memory --help` run |
| P1-2 | Replace the `pydantic<2.6` cap with a floor only, or per-interpreter markers | Install succeeds on every Python in the support matrix, including 3.13 |
| P1-3 | Decide the support matrix (D2) and make `requires-python`, `CLAUDE.md`, ruff `target-version` and CI agree | CI has a job for the lowest and highest supported versions, both green |
| P1-4 | Add fastapi (and what the three failing files need) to `dev`, or skip those tests cleanly when it is absent | `pytest` collects with zero errors |
| P1-5 | Install smoke test in CI: build the wheel, install into an empty environment, run `WARNETECH_OFFLINE=1 warnetech ask hello` | Job fails if the entry point or a dependency pin breaks again |
| P1-6 | Correct `CLAUDE.md` (dependency rule, Python version, test count) | Doc matches `pyproject.toml` |
| P1-7 | Tag `0.1.1` | Wheel installs from the tag |

P1-5 is the important one: the two install bugs shipped because nothing ever installed the built package.

### Phase 2: a CLI worth using daily

| ID | Work | Accept when |
|---|---|---|
| P2-1 | Read the prompt from stdin (`cat file \| warnetech ask "summarise"`) | Works with and without a positional question |
| P2-2 | Streamed output for `ask` and `chat` where the provider supports it | First token prints before the full reply is ready; echo provider covers it in tests |
| P2-3 | `--system`, `--json`, and stable exit codes across commands | Documented and tested |
| P2-4 | `warnetech logs`: list recent calls with model, tokens and cost from the usage store | Reads existing data; no schema change |
| P2-5 | First-run path: `warnetech doctor` names the missing key or the local model to pull | A new user reaches a real answer by following only its output |
| P2-6 | Memory default: local SQLite, on unless `--no-save`; write-time dedup | Second identical `remember` stores nothing new |

### Phase 3: agents and tools

| ID | Work | Accept when |
|---|---|---|
| P3-1 | Conformance-test the MCP server against at least two real clients | Recorded transcript per client in tests |
| P3-2 | MCP client so `agi/tools.py` can call outside servers, with an allowlist | Tool results are treated as data; a hostile tool description cannot trigger a call |
| P3-3 | One agent command (`warnetech run "<task>"`) with a hard budget in tokens and steps | Stops at the budget and reports spend |
| P3-4 | Memory reconciliation on write (add, update, or skip) | Measured on a fixed set of our own conversations, before and after |

### Phase 4, only if demand appears

Optional LiteLLM provider extra; sqlite-vec index; packaging for Termux; published docs site.

### 5.3 Codeflash, alongside Phase 1 and 2

Blocked on P1-1 and P1-2, since it has to import and test the package.

1. Install as a dev-only tool (not in any extra that users install).
2. Run on the pure-Python hot paths first: BM25 scoring in `memory/warnetech_memory.py`,
   `chunk_text` in `memory/ingest.py`, the token bucket in `resilience.py`, cost maths in `usage.py`.
3. Review every proposed change by hand. A speed-up that the tests pass is only as safe as the tests.
4. The GitHub Action that optimises each new pull request needs the Codeflash GitHub App installed
   on the repository and a repository secret. That is an owner decision (D4).

## 6. Decisions needed

- **D1.** Which product is this repository? `docs/PRD.md` specifies an air-gapped defense engine with
  no cloud calls; this document and `CLAUDE.md` specify a gateway with hosted providers. They can
  coexist only if the defense engine is a separate package that never imports the gateway.
- **D2.** Lowest supported Python. 3.8 is the last version that runs on Windows 7 and has been out of
  security support since October 2024. Supporting it caps several dependencies and needs a CI job
  that does not exist. Recommendation: 3.10 to 3.13 as the tested matrix; treat 3.8 as best-effort
  and say so, unless Windows 7 is a hard requirement.
- **D3.** Confirm dropping Click/Typer and Chroma from the plan.
- **D4.** Allow Codeflash to open pull requests on this repository, and install its GitHub App?

## 7. Risks

- Old-interpreter support is claimed but untested; the pin added for it broke new interpreters.
- Scope: about 800 tests cover a wide surface (interpreter, custom models, routing, UI) for a 0.1
  release. Phases 1 and 2 deliberately add almost nothing.
- The research in section 3 leans partly on vendor-written comparisons and on recall. Re-check the
  "Recalled" rows before acting on them.

## 8. Sources read this session

- https://www.swarms.ai/blog/best-llm-gateway-python (vendor-authored)
- https://llmgateway.io/blog/litellm-alternatives (vendor-authored)
- https://www.getmaxim.ai/articles/best-llm-gateways-for-coding-agents-in-2026/ (vendor-authored)
- https://www.digitalapplied.com/blog/open-source-agent-memory-mem0-letta-zep-compared
- https://infranodus.com/docs/llm-memory-tools-comparison
- https://github.com/codeflash-ai/codeflash
- https://docs.codeflash.ai/optimizing-with-codeflash/codeflash-github-actions
