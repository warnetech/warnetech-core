# Warnetech-Core PRD: job catalogue

Status: draft for review. Documentation only; no code was written, no package installed, no network call made while producing it.
Facts about the repo come from `ls -la`, `git status`, `grep` and the files named below. Anything not checked is marked `UNKNOWN`.

## 1. Purpose and scope

Warnetech-Core is a local, air-gapped input-defense engine with local memory, local anomaly detection and optional local AI. It takes an untrusted payload, decides ALLOW or BLOCK from signatures and libinjection, learns from reported outcomes, and records everything in a local execution log. Worker nodes and an orchestrator pass files to each other on one machine.

**Out of scope:** cloud services (Cloudflare, Supabase, third-party connectors), network IPC between nodes, any hosted model, any remote vector database, and application code in this task.

## 2. Governing constraints

Copied from the project directive:

- Air-gapped and self-reliant. No external network calls, remote vector databases, or cloud APIs. Local binaries, flat `.jsonl` files and embedded runtimes only.
- All internal names, databases, configs and services use the `warnetech_` prefix.
- Python 3.10+ for the application. Strict typing.
- Memory persistence and retrieval use `data/rag_store.jsonl`.
- Execution logs use local SQLite or JSON-lines streams.
- CLI tools, worker nodes and orchestration layers stay modular.

## 3. Existing assets

**Inputs checked.** `ls -la` and `git status` were run in `/home/user/warnetech-core` (branch `claude/warnetech-core-agi-setup-2x0okx`, clean at the time). The working directory `/home/user` is not itself a git repository.

**Missing inputs.**
- The "Warnet Control Plane" plan was **not supplied** in the message this PRD was written from. Section 4 uses only the facts the task restated from it. Every claim about that plan is `UNKNOWN`.
- There is no earlier PRD in `docs/`. Two unrelated PRDs are vendored (`vendor/cli-ai-command-center-prd/CLI-AI-Command-Center-PRD.md` and a copy under `vendor/claude-code-assist/packages/aicli/`); they describe a different product and were not used.
- `docs/WARNETECH-ALIGNMENT.md` exists. It was **not read** for this PRD (`UNKNOWN` contents).

**What exists and can be reused** (all under `/home/user/warnetech-core`):

| Asset | Path | Reuse |
|---|---|---|
| Command-group framework, max 4 parameters per command | `src/warnetech_core/cli_core.py` | CLI shape for `warnetech_defense` |
| Append-only JSONL writer that stores a SHA-256 and a truncated preview and never writes environment or output | `src/warnetech_core/interpreter/audit.py` | Pattern for LOG-001 and LOG-006 |
| SQLite stores (memory, usage) | `src/warnetech_core/memory/sqlite.py`, `usage.py` | Pattern for LOG-002 |
| Paragraph-aware chunker with overlap | `src/warnetech_core/memory/ingest.py` (`chunk_text`) | MEM-002 |
| Flat-file store | `src/warnetech_core/memory/rag_store.py` | See gap below |
| Environment-variable settings and `.env.example` discipline | `config.py`, `.env.example` | CFG-001 |
| Environment report (`warnetech doctor`) including Android and 32-bit detection | `src/warnetech_core/runtime/doctor.py` | Health check for new nodes |
| Ollama provider over HTTP | `src/warnetech_core/providers/ollama.py` | Base for AI-002; **needs a loopback guard** (it accepts LAN nodes via `OLLAMA_NODES`) |
| Offline test suite, 824 tests passing at last run, echo provider | `tests/` | Test style and fixtures |
| CI matrix: Ubuntu Python 3.10 to 3.12, arm64 Linux, Windows; macOS queued but never got a runner | `.github/workflows/ci.yml` | TST-009 |
| Install verified once on 64-bit Android (Debian under proot, and native Termux) | `docs/DEPLOY.md` | Platform baseline |
| Offline install from a wheel directory with `--no-index` (24 wheels, verified once for this repo) | earlier session, no script committed | PKG-002 |

**Gaps found.**
- No defense code, signature file, scoring, or `warnetech_defense` command exists.
- `libinjection`, `pyod` and `sqlite-vec` appear in no dependency list and no code. `sqlite-vec` is mentioned only in `docs/MEMORY.md` as not used.
- `data/rag_store.jsonl` does not exist. The existing `RAGStore` uses `./data/rag_memory/index.jsonl` (used by `scripts/main.py`); whether it tolerates corrupt lines is `UNKNOWN` (not read in full). The current memory feature writes SQLite (`data/warnetech_memory.db`), not this file.
- `data/` is git-ignored; it holds `warnetech_memory.db` and `warnetech_usage.db` only.
- No test blocks outbound sockets.
- No type checker is in the dev dependencies or CI (`ruff` only). Editor configs: `UNKNOWN`.
- The repo's own CLAUDE.md describes a shared AI gateway with hosted providers and an optional Supabase backend. That conflicts with the air-gapped directive (see Decision D1).
- Existing module names are `warnetech_core.*`. Names such as `cli.py`, `chat.py` carry no prefix inside the package.

## 4. Job catalogue

Each job: **ID, Phase, Title.** Description. In: / Out:. Accept: (checkable). Deps. Status.
Numbers marked *proposed* are my choices where the directive asked me to define a value; confirm or change them in review.

### A. Defense (DEF)

**DEF-001 · P0 · Payload intake and normalization.** Read a payload from `--text`, `--file`, or stdin and produce one normalized string.
In: raw bytes. Out: normalized text plus a record of each transform applied.
Accept: URL-decoding runs repeatedly up to 3 passes and stops when output stops changing; text is lowercased; runs of whitespace collapse to one space; NUL bytes are removed and flagged; NFKC Unicode normalization is applied; `%2527` and `%27` and `'` all normalize to the same output; invalid UTF-8 is replaced, not raised.
Deps: SEC-001. Status: TODO.

**DEF-002 · P1 · libinjection SQLi and XSS detection with fingerprint capture.** Run the normalized payload through the Python bindings; keep the SQLi fingerprint.
In: normalized text. Out: `{sqli: bool, xss: bool, fingerprint: str|None}`.
Accept: a known SQLi sample sets `sqli` true and stores a non-empty fingerprint; a known XSS sample sets `xss` true; benign text sets both false; if the library is not installed the job reports `libinjection_unavailable` and the defense path continues on signatures. A libinjection hit contributes a signature component of 0.85 (*proposed*). Whether XSS returns a fingerprint is `UNKNOWN`; verify against the binding. Binding package name and version: `UNKNOWN` (see PKG-001).
Deps: DEF-001, PKG-004. Status: TODO.

**DEF-003 · P0 · Signature file schema and loader.** `data/signatures.jsonl`, one JSON object per line.
Fields (exactly): `id`, `attack_type`, `pattern`, `weight`, `confidence`, `occurrences`, `false_positives`.
Accept: loader validates types and ranges (`weight` and `confidence` in 0..1, counts non-negative integers, `pattern` compiles, `id` matches DEF-004); unknown fields are rejected; duplicate ids are rejected; the old Python engine's schema is stated to match, but that engine is not in this repo, so the match is `UNKNOWN` until a sample file is supplied.
Deps: DEF-004. Status: TODO.

**DEF-004 · P0 · Stable signature id scheme.** `sig_<attack_type>_<sha1[:10]>`.
Accept: the hash input is the UTF-8 bytes of the pattern text exactly as stored; the same pattern always gives the same id on every platform; `attack_type` is lowercase `[a-z_]+`; changing one character of the pattern changes the id; a test fixes five ids as literals.
Deps: none. Status: TODO.

**DEF-005 · P0 · Regex signature matching.** Match the normalized text against every signature.
Out: list of matched signature ids with weights.
Accept: patterns longer than 512 characters are rejected at load; payload length is capped by SEC-001 before matching; a pathological-pattern fixture (nested quantifiers) completes in under 1 second on the capped payload or the signature is refused at load (ReDoS control; the method is a risk, see section 7); each match increments `occurrences` in memory and is persisted by DEF-016.
Deps: DEF-001, DEF-003. Status: TODO.

**DEF-006 · P0 · Seed signature set.** A small starting file for SQLi and XSS, authored for this project.
Accept: at least 10 signatures per attack type, each with a comment-free JSON line, initial `weight` 0.70, `confidence` 0.50, counts 0 (*proposed*); every golden-file attack in TST-005 matches at least one; provenance is "written for this project"; the old engine's signature set is `UNKNOWN` and is not copied.
Deps: DEF-003, DEF-004. Status: TODO.

**DEF-007 · P0 (signature only) / P2 (full) · Scoring.** `total = signature + behavioral*0.3 + anomaly*0.2`, then clamped to 0..1.
Accept: `signature` is the highest `weight` among matched signatures and the DEF-002 floor, else 0; in Phase 0 `behavioral` and `anomaly` are 0; each input is clamped to 0..1 before use; the formula is a pure function with unit tests including the clamp and all-zero cases.
*Why the older weighting was rejected:* the older weighting is `UNKNOWN` (not in this repo; plan not supplied). The directive's rationale for the new one is that the signature is primary. My inference, not a stated source: signatures are deterministic and auditable, while behavioral and anomaly inputs are statistical and noisy, so they may nudge a decision but must not carry it alone. With these weights, behavioral plus anomaly can add at most 0.5, so they can move a borderline case but cannot BLOCK a payload with no signature hit unless both are near maximum (0.5 < 0.60). Record the real reason once the older weights are supplied.
Deps: DEF-005. Status: TODO.

**DEF-008 · P0 · Threat levels and decision thresholds** (*proposed*).
Levels by `total`: NONE under 0.20; LOW 0.20 to under 0.40; MEDIUM 0.40 to under 0.60; HIGH 0.60 to under 0.80; CRITICAL 0.80 and above. Decision: **BLOCK at 0.60 and above, ALLOW below.**
Accept: boundary tests at 0.1999, 0.20, 0.5999, 0.60, 0.7999, 0.80 and 1.0 give the stated level and decision; cutoffs read from config (CFG-001) with these as defaults; one unmatched seed signature at weight 0.70 blocks, and after three false positives (0.70 to 0.55) it no longer blocks.
Deps: DEF-007. Status: TODO.

**DEF-009 · P2 · Behavioral score source.** What feeds `behavioral`.
The directive gives its weight (0.3) but not its definition. Proposal: count of BLOCK or HIGH events in the execution log from the same `source` id within a window, mapped to 0..1.
Accept: defined and agreed before implementation; `source` is an optional caller-supplied string, never an IP read from the network. Status: DECISION-NEEDED.
Deps: LOG-002, DEF-007.

**DEF-010 · P0 · Fail-closed behavior.** If the signature file is missing, unreadable, or fails validation, the decision is BLOCK.
Accept: `analyze` prints decision BLOCK with reason `signature_store_unavailable` and exits with code 3; the event is logged (LOG-003); no ALLOW is possible in this state; a test removes the file and a second test corrupts one line and both get exit 3. Whether a single corrupt line should fail the whole file or be skipped is open (Decision D6); this job implements fail-closed on any corrupt line.
Deps: DEF-003, LOG-003. Status: TODO.

**DEF-011 · P1 · Outcome reporting and reinforcement.** `outcome` reports whether a decision was a true positive or a false positive for each matched signature.
Formulas as given: true positive `weight = min(0.95, weight + (0.95 - weight) * 0.08)`; false positive `weight = max(0.10, weight - 0.05)`; `confidence = max(0.10, min(0.98, weight * (1 - fp / occurrences)))`.
Accept: `occurrences` equal to 0 uses a false-positive ratio of 0 (no division by zero); 0.50 plus one true positive gives 0.536; 0.50 minus one false positive gives 0.45; weight never exceeds 0.95 or falls below 0.10 after 1,000 repeated outcomes; with weight 0.50, occurrences 10, fp 2 the confidence is 0.40; `false_positives` increments only on a false-positive report; an outcome for an unknown correlation id is rejected with exit code 2.
Deps: DEF-003, LOG-003, DEF-016. Status: TODO.

**DEF-012 · P1 · Minting a new signature.** Only when no signature matched, and never a duplicate.
Accept: a mint request with a matched payload creates nothing; the same pattern submitted twice yields one entry (checked by DEF-004 id); the new entry starts at the DEF-006 initial values. What the minted pattern is built from (a libinjection fingerprint, a token-shape pattern, or an escaped literal of the payload) is not defined by the directive; an escaped literal over-fits and may store sensitive text. Status: DECISION-NEEDED.
Deps: DEF-011, DEF-002.

**DEF-013 · P0 · CLI `warnetech_defense analyze`.**
In: payload source plus optional `--source`. Out: JSON `{cid, decision, level, scores, signature_ids, fingerprint}` on stdout.
Accept: exit 0 for ALLOW, 1 for BLOCK, 2 for usage error, 3 for fail-closed condition; output is valid JSON in every case; runs on Debian, Termux and Windows.
Deps: DEF-001, DEF-005, DEF-007, DEF-008, DEF-010. Status: TODO.

**DEF-014 · P1 · CLI `warnetech_defense outcome`.** Arguments: correlation id, `tp` or `fp`. Accept: calls DEF-011; prints the changed signatures; exit codes as DEF-013.
Deps: DEF-011. Status: TODO.

**DEF-015 · P0 · CLI `warnetech_defense list`.** Prints signatures with id, type, weight, confidence, counts; `--json` available. Accept: output order is stable (by id); an empty file prints an empty list and exits 0; a missing file exits 3.
Deps: DEF-003. Status: TODO.

**DEF-016 · P1 · Safe signature-file writes.** Accept: writes go to a temporary file in the same directory and are renamed over the original; two concurrent writers cannot lose an update (file lock, working on Linux, Termux and Windows); a kill during write leaves either the old or the new complete file, tested by a fault-injection test.
Deps: DEF-003. Status: TODO.

### B. Execution log and observability (LOG)

**LOG-001 · P0 · JSON-lines event stream** at `data/warnetech_exec_log.jsonl`.
Event fields: `ts` (UTC ISO 8601), `event` (`analyze`, `outcome`, `error`, `anomaly`), `cid`, `decision`, `level`, `scores`, `signature_ids`, `payload_sha256`, `payload_preview` (at most 256 characters), `duration_ms`, `schema` (integer).
Accept: one valid JSON object per line; append-only; a record never exceeds 8 KiB; a test reads the file back and validates every line.
Deps: none. Status: TODO.

**LOG-002 · P1 · SQLite table `warnetech_exec_log`.** Columns mirror LOG-001; indexes on `ts`, `cid`, `event`, `decision`.
Accept: created by a versioned migration function (no ad-hoc DDL in request paths); a lookup by `cid` uses the index (checked with `EXPLAIN QUERY PLAN`); WAL mode on; the database path comes from config.
Deps: LOG-001. Status: TODO.

**LOG-003 · P0 · Log every analyze, outcome and error.** Each carries a timestamp and a correlation id (UUID4) generated at the start of the command.
Accept: one `analyze` call writes exactly one event; a raised error writes an `error` event with an error class and no stack of user data; the `cid` returned by `analyze` is the one logged; a logging failure never changes the decision (decision still printed, warning on stderr, exit code unchanged).
Deps: LOG-001. Status: TODO.

**LOG-004 · P1 · Rotation and size cap** (*proposed*). JSONL files rotate at 10 MiB and 5 rotated files are kept; the SQLite log keeps 90 days and at most 500,000 rows, deleting oldest first.
Accept: a test writes past each limit and the oldest data is gone, newest intact; rotation never splits a line; limits come from config.
Deps: LOG-001, LOG-002. Status: TODO.

**LOG-005 · P1 · Log read commands.** `warnetech_defense log tail` and `log show <cid>`.
Accept: `tail -n 20` returns the last 20 events in order; `show` returns all events for one cid; both work when only the JSONL exists.
Deps: LOG-001. Status: TODO.

**LOG-006 · P0 · Log content safety.** Payloads can hold secrets or attack strings.
Accept: only a SHA-256 and a preview of at most 256 characters are stored, never the full payload; the preview strips control characters; environment variables and AI replies are never logged.
Deps: LOG-001. Status: TODO.

### C. Memory and retrieval (MEM)

**MEM-001 · P1 · `data/rag_store.jsonl` schema and append-only rule.**
Fields: `id`, `ts`, `source`, `text`, `sha256`, `meta` (object). Deletion is a new `{"id": ..., "deleted": true}` record (tombstone), never an edit.
Accept: the file is only ever opened in append mode by the writer; the loader resolves tombstones; a test shows an entry deleted and still recoverable from the raw file. The existing `RAGStore` default path is `data/rag_memory/index.jsonl`; a migration or an explicit path change is part of this job.
Deps: none. Status: TODO.

**MEM-002 · P1 · Ingest into the JSONL store.** Reuse `chunk_text` for splitting.
Accept: a file becomes chunks with `source` set to the file name; hidden files, likely-secret names, binaries and files over 1 MB are skipped and counted; re-ingesting the same file adds nothing (MEM-003).
Deps: MEM-001, MEM-003. Status: TODO.

**MEM-003 · P1 · Deduplication.** Key is the SHA-256 of whitespace-normalized lowercase text.
Accept: the same text under two sources is stored once with both sources recorded in `meta`; a one-character change is stored separately.
Deps: MEM-001. Status: TODO.

**MEM-004 · P1 · Keyword retrieval.** Standard library only: tokenize, score by term frequency weighted by inverse document frequency.
Accept: the top-k (default 5) are returned best first; stop words are ignored; an empty query returns nothing; a 10,000-record file answers in under 1 second on a laptop (measured, not assumed).
Deps: MEM-001. Status: TODO.

**MEM-005 · P1 · Context retrieval function for the CLI.** `get_context(query, max_chars)` returns text plus sources.
Accept: output is capped at `max_chars`; every returned chunk names its source; retrieved text is labeled as data, not instructions; tombstoned records never appear.
Deps: MEM-004. Status: TODO.

**MEM-006 · P3 · `sqlite-vec` index rebuilt from the JSONL.**
Accept: a rebuild command recreates the index from `data/rag_store.jsonl` alone and a deleted index changes no answers; if the loadable-extension feature is unavailable in the Python build, the job reports it and retrieval falls back to MEM-004; embeddings come from a local model only. Availability on Termux, Windows and the system Python of Debian is `UNKNOWN`; many Python builds disable loading extensions.
Deps: MEM-001, MEM-004, AI-002. Status: TODO.

**MEM-007 · P1 · Corrupt-line tolerance for memory.** Accept: an unparseable line is skipped, counted, and reported once per load; the rest of the file loads. Memory is not a security gate, so this differs from DEF-010.
Deps: MEM-001. Status: TODO.

### D. Anomaly detection (ANO)

**ANO-001 · P2 · Metric ingest.** From `data/warnetech_metrics.jsonl`; fields `ts`, `name`, `value`, `tags` (object).
Accept: invalid lines are skipped and counted; non-finite values are rejected; a file of 100,000 lines loads without reading all into memory at once.
Deps: none. Status: TODO.

**ANO-002 · P2 · Rollups.** Per metric name and window: count, min, max, mean, p50, p95, p99, standard deviation; local only.
Accept: percentiles use linear interpolation between ranks (inclusive method); results match hand-computed values on a fixture of 20 numbers; population versus sample standard deviation is stated in the output schema (sample, *proposed*); an empty window yields count 0 and nulls.
Deps: ANO-001. Status: TODO.

**ANO-003 · P2 · PyOD ECOD detector.** Separate train and score steps.
Accept: `train` reads a baseline window and stores a baseline; `score` never changes the baseline; scoring uses only the stored baseline; scores normalized to 0..1 for DEF-007. PyOD and its numeric dependencies are heavy and their current versions, Python 3.10 support and the exact ECOD interface are `UNKNOWN` until looked up (PKG-001); the dependency is an optional extra.
Deps: ANO-002, PKG-004. Status: TODO.

**ANO-004 · P2 · Baseline storage.** Accept: baseline is stored without `pickle` of untrusted data (store the training window and parameters, retrain at load, or verify a SHA-256 over a model file written by this tool); a tampered baseline is detected and refused.
Deps: ANO-003. Status: TODO.

**ANO-005 · P2 · Anomaly records to the execution log.** Accept: each anomaly above threshold writes an `anomaly` event with metric name, value, score and cid.
Deps: ANO-003, LOG-003. Status: TODO.

**ANO-006 · P2 · Threshold configuration file.** Keys with the `warnetech_` prefix, in the single config (CFG-001). Accept: invalid values refuse to start with a message naming the key.
Deps: CFG-001. Status: TODO.

**ANO-007 · P2 · Anomaly input to scoring.** Accept: `anomaly` in DEF-007 is the latest normalized score for the payload's metric context, or 0 when none; absence of a baseline yields 0 and a logged note.
Deps: ANO-003, DEF-007. Status: TODO.

### E. Local AI (AI)

**AI-001 · P2 · Provider interface.** A small interface (`complete(prompt) -> text`, `healthy() -> bool`).
Accept: a second runtime can be selected by config key `warnetech_ai_provider` with no code change; a fake provider in tests satisfies the same interface.
Deps: CFG-001. Status: TODO.

**AI-002 · P2 · Ollama adapter.** Calls `http://127.0.0.1:11434/v1/chat/completions` (the host comes from config).
Accept: request and reply handled for success, timeout and malformed replies; no fallback to any hosted provider exists in the code path (checked by AI-003 and TST-007).
Deps: AI-001, AI-003. Status: TODO.

**AI-003 · P2 · Loopback-only enforcement.** Accept: hosts `127.0.0.1`, `::1` and `localhost` (resolved) are allowed; any other host, including a LAN address or a name resolving to one, is refused before any connection and the refusal is logged; tests cover `10.0.0.5`, `0.0.0.0`, a public name and a hostname that resolves to a non-loopback address. The existing `OLLAMA_NODES` feature is not used by this path.
Deps: none. Status: TODO.

**AI-004 · P2 · Degraded behavior.** Accept: with the AI stopped, `analyze` returns the same decision as with it running (signatures and libinjection only), logs `ai_unavailable` once, and exits with the normal code; a test with a dead port proves it.
Deps: AI-002, DEF-013. Status: TODO.

**AI-005 · P3 · Second runtime swap (e.g. llamafile).** Accept: changing `warnetech_ai_provider` and the host in config selects it; the same contract tests pass for both; no other file changes.
Deps: AI-001, AI-003. Status: TODO.

**AI-006 · P2 · Role of AI in the defense decision.** The directive does not say what the AI does for the defense path. Proposal: advisory only (plain-language explanation of a block); the AI never changes ALLOW or BLOCK.
Accept: decided before AI-002 is wired into `analyze`. Status: DECISION-NEEDED.
Deps: none.

### F. Orchestration and worker nodes (ORC)

**ORC-001 · P2 · Worker node CLI contract.** Every node takes `--in <file>` and `--out <file>`, reads one input file, writes one output file.
Exit codes: 0 success, 1 BLOCK or a negative result, 2 usage error, 3 fail-closed condition, 4 timeout.
Accept: every node follows the contract; stdout is human text only, results go in the output file; a contract test runs against each node.
Deps: DEF-013. Status: TODO.

**ORC-002 · P2 · Orchestrator** (`warnetech_orchestrator`). Runs jobs from a manifest in order and records each status.
Accept: a failed job stops the run unless the manifest marks it `continue_on_error`; status for every job (`pending`, `ok`, `failed`, `skipped`) is written to the execution log; a rerun skips jobs already `ok` when `--resume` is given.
Deps: ORC-001, ORC-004, LOG-003. Status: TODO.

**ORC-003 · P2 · File handoff.** Accept: a node writes its output to a temporary name and renames it, so a reader never sees a partial file; no sockets, pipes between hosts, or shared memory are used; a test confirms no network is opened (TST-007).
Deps: ORC-001. Status: TODO.

**ORC-004 · P2 · Job manifest format.** A YAML or JSONL file listing job id, node, input, output, timeout. Accept: validated before the run; unknown nodes are refused; output paths must stay inside a configured work directory.
Deps: CFG-001. Status: TODO.

**ORC-005 · P3 · Timeouts and retries.** Accept: a node that exceeds its timeout is killed and recorded with code 4; retry count is bounded (default 1) and only for nodes marked idempotent.
Deps: ORC-002. Status: TODO.

### G. Configuration, security and packaging (CFG / SEC / PKG)

**CFG-001 · P0 · Single config file with environment overrides.** One file, `config/warnetech.yaml`, keys prefixed `warnetech_` (for example `warnetech_signatures_path`, `warnetech_block_threshold`, `warnetech_max_payload_bytes`). YAML is chosen because PyYAML is already a required dependency and Python 3.10 has no TOML reader in the standard library.
Accept: an environment variable named as the upper-cased key overrides the file; precedence is environment, then file, then default; every key is listed in `.env.example` or the config docs; an unknown `warnetech_` key is an error.
Deps: none. Status: TODO.

**CFG-002 · P0 · Config validation.** Accept: types and ranges are checked at startup; a bad value exits 2 naming the key; thresholds must satisfy `0 <= level cutoffs ascending <= 1`.
Deps: CFG-001. Status: TODO.

**SEC-001 · P0 · Input size limits** (*proposed*). Payload at most 64 KiB; decoded payload at most 4 times the input and never above 256 KiB; matching runs only on the capped text.
Accept: an oversize payload is refused with exit 2 and logged (hash only); a decode-bomb fixture (`%25` repeated) stays under the limit.
Deps: none. Status: TODO.

**SEC-002 · P0 · Safe parsing of untrusted data.** Accept: no `eval`, `exec`, `pickle` or unsafe YAML loading anywhere on the payload or config path (a grep-based test enforces it); only `yaml.safe_load`; all file paths from manifests or arguments are resolved and checked to stay inside allowed directories.
Deps: none. Status: TODO.

**SEC-003 · P0 · Secrets supplied locally.** Accept: secrets come from environment variables only; no secret value appears in any repo file, config, log or backup; the docs say how to set variables in Termux, Debian and Windows; a scan test fails on common secret patterns in tracked files.
Deps: none. Status: TODO.

**SEC-004 · P0 · BLOCKING: rotate the leaked Supabase secret key.** The task states the reference plan contains a live Supabase secret key in plain text.
Accept: the key is rotated in the Supabase dashboard by its owner (the agent cannot do this); the plan file is purged or the leaked value removed; the key is not copied into any file. Whether the key exists in this repo: a search of tracked files for common key patterns found nothing (file names only; no values read), but the plan itself was not supplied, so the key's presence elsewhere is `UNKNOWN`. Until done, no cloud job (CLD-*) may start.
Deps: none. Status: TODO (blocking).

**SEC-005 · P1 · Secret scan before commit.** Accept: a local check fails the commit on a high-entropy or known-pattern secret; the check needs no network.
Deps: SEC-003. Status: TODO.

**PKG-001 · P1 · Pinned dependency list.** Accept: every runtime and optional dependency is pinned to an exact version in a lock file; each entry records the date its version was checked and a vulnerability check result; the lookup is done once on a connected build machine and recorded, since this system itself never goes online. Current versions and maintenance status of libinjection bindings, PyOD, sqlite-vec and their transitive dependencies are `UNKNOWN`; I could not check them offline. Anything stale, niche or with a known issue is flagged for approval rather than added.
Deps: none. Status: TODO.

**PKG-002 · P1 · Offline install procedure.** Accept: a documented script builds a directory of wheels on a connected machine; a fresh virtual environment on an air-gapped test machine installs from it with `--no-index`; the result passes the test suite; covered for Debian and Termux, Windows documented separately.
Deps: PKG-001. Status: TODO.

**PKG-003 · P1 · Packaging layout and names.** Modules proposed as `warnetech_defense`, `warnetech_log`, `warnetech_memory`, `warnetech_anomaly`, `warnetech_ai`, `warnetech_orchestrator`. Whether these are new top-level packages or subpackages of the existing `warnetech_core` is open (Decision D5).
Accept: names settled before code; no unprefixed module is added.
Deps: none. Status: DECISION-NEEDED.

**PKG-004 · P1 · Optional extras.** Accept: libinjection, PyOD and sqlite-vec are extras imported only when used; with none installed the Phase 0 and Phase 1 core features work; each missing extra gives a clear message and a degraded path.
Deps: none. Status: TODO.

### H. Backup and export (BKP)

**BKP-001 · P2 · Local archive.** Archive `data/` and the SQLite log into one file with a manifest of paths and SHA-256 hashes.
Accept: the manifest lists every file with size and hash; the archive is created under a configured backup directory; no secret values are included (config is archived with values of secret-named keys removed).
Deps: LOG-002, MEM-001. Status: TODO.

**BKP-002 · P2 · Restore procedure, tested on a copy.** Accept: restoring into an empty directory reproduces every file with a matching hash; a corrupted archive is detected by hash mismatch and refused; the test never touches the live `data/`.
Deps: BKP-001. Status: TODO.

**BKP-003 · P2 · `docs/WARNETECH-CONFIG-BACKUP.md` generated from real state.** Names and locations only.
Accept: generated by a command from the actual config, file list and modules; contains no secret value (the generator never reads secret variables, and a test scans the output); regenerating twice on unchanged state gives identical output except the timestamp.
Deps: CFG-001, BKP-001. Status: TODO.

**BKP-004 · P2 · Consistent SQLite snapshot.** Accept: the log database is copied with SQLite's backup mechanism so a write in progress cannot produce a torn file; verified by writing during backup.
Deps: LOG-002. Status: TODO.

### I. Testing (TST)

**TST-001 · P0 · Scoring math tests.** Accept: cases for each term, the clamp, weights 0.3 and 0.2, all zeros, and all ones.
Deps: DEF-007. Status: TODO.

**TST-002 · P1 · Reinforcement math tests.** Accept: the worked values in DEF-011; floor and ceiling after many repeats; zero occurrences; property check that weight stays in 0.10..0.95 and confidence in 0.10..0.98 for random sequences.
Deps: DEF-011. Status: TODO.

**TST-003 · P0 · Id scheme tests.** Accept: five fixed literal ids; stability across two runs; sensitivity to a one-character change.
Deps: DEF-004. Status: TODO.

**TST-004 · P0 · Decision threshold tests.** Accept: every boundary value in DEF-008.
Deps: DEF-008. Status: TODO.

**TST-005 · P0 · Golden-file payload tests.** A fixed list in `tests/data/warnetech_golden.jsonl` with the expected level and decision.
Accept: at least 20 payloads in Phase 0 (SQLi, XSS, encoded variants, benign text, edge cases); the test fails on any change in result; the file grows with each missed attack found.
Deps: DEF-013, DEF-006. Status: TODO.

**TST-006 · P0 / P1 · Fail-soft drills.** P0: missing signature file gives exit 3 and BLOCK. P1: one corrupt signature line, a corrupt `rag_store.jsonl` line (skipped), and AI unavailable (same decision).
Accept: each drill is its own test with the expected exit code and log event.
Deps: DEF-010, MEM-007, AI-004. Status: TODO.

**TST-007 · P0 · Offline check.** Accept: a test replaces socket creation so any attempt to open one fails the test; the whole defense path (analyze, outcome, list, log, retrieval, orchestrator) runs under it; only the AI adapter tests use a fake local server and only on loopback.
Deps: none. Status: TODO.

**TST-008 · P1 · Guard sabotage checks.** Accept: for each safety guard (fail-closed, size limit, loopback check, secret skip, append-only), a test fails when the guard is disabled, shown once in review notes.
Deps: DEF-010, SEC-001, AI-003. Status: TODO.

**TST-009 · P1 · Platform matrix.** Accept: tests run in CI on Linux (Python 3.10, 3.11, 3.12), Linux arm64 and Windows; Termux and Debian-on-Android run by hand per release; macOS is reported as not run until a runner exists.
Deps: none. Status: TODO.

**TST-010 · P1 · Strict typing gate.** Accept: a type checker in strict mode passes on the new modules in CI. Which checker (mypy, pyright) and its current version are `UNKNOWN`; needs approval under PKG-001.
Deps: PKG-001. Status: TODO.

### J. Documentation (DOC)

**DOC-001 · P0 · README.** Accept: install, quick start and the three CLIs with example commands that run as written. The directive says "the three CLIs" without naming them; assumed to be `warnetech_defense`, a worker node CLI and the orchestrator CLI (`UNKNOWN`, confirm).
Deps: DEF-013. Status: TODO.

**DOC-002 · P0 · Architecture diagram.** Accept: a Mermaid diagram of the local data flow (payload, normalize, match, score, decide, log, outcome, retrain) that matches the code; reviewed whenever a module is added.
Deps: none. Status: TODO.

**DOC-003 · P1 · Operator runbook.** Accept: steps for recovering from fail-closed, rotating a secret, restoring a backup, and reading the log, each tried once on a test copy.
Deps: BKP-002, LOG-005. Status: TODO.

### K. Deferred cloud jobs (CLD)

All `DEFERRED — pending decision`. They come from the reference plan, are recorded only, and are not designed. None may start before SEC-004 is done and Decision D1 is answered.

- **CLD-001 · P4 · Cloudflare Worker routes, D1 cache, R2 archive, KV rate-limit counters.** Reason: needs a cloud account and network access, conflicts with the air-gapped directive.
- **CLD-002 · P4 · Supabase Postgres schema, pg_cron jobs, pg_partman, pgtap, RLS.** Reason: remote database; also the repo rule says never issue DDL from code and use one shared project.
- **CLD-003 · P4 · Hyperdrive or Data API connection.** Reason: depends on CLD-001 and CLD-002.
- **CLD-004 · P4 · Quota watchdogs and breakers tied to Cloudflare or Supabase limits.** Reason: meaningless without the cloud services.
- **CLD-005 · P4 · Third-party connectors** (Malwarebytes, HIBP, Norton, Hugging Face, Dropbox, Gmail, Zapier, Railway, Vercel, Postman, Mem0, Privacy.com). Reason: all external network calls and credentials.
- **CLD-006 · P4 · Cloud backup document and offline PDF.** Reason: depends on the cloud jobs above.

## 5. Phase plan

| Phase | Goal | Jobs |
|---|---|---|
| **0 — quick win** | `warnetech_defense analyze` returns ALLOW or BLOCK on a payload, from signatures only, fail-closed, logged, no new dependencies | DEF-001, 003, 004, 005, 006, 007, 008, 010, 013, 015; LOG-001, 003, 006; CFG-001, 002; SEC-001, 002, 003, 004; TST-001, 003, 004, 005, 006 (missing file), 007; DOC-001, 002 |
| 1 | Learning, memory, SQLite log, libinjection, safe operation | DEF-002, 011, 012, 014, 016; LOG-002, 004, 005; MEM-001 to 005, 007; PKG-001 to 004; SEC-005; TST-002, 006, 008, 009, 010; DOC-003 |
| 2 | Anomaly detection, local AI, nodes, backup | DEF-009; ANO-001 to 007; AI-001 to 004, 006; ORC-001 to 004; BKP-001 to 004; DEF-007 full form |
| 3 | Vector search, second runtime, retries | MEM-006, AI-005, ORC-005 |
| 4 | Cloud jobs, only if Decision D1 changes | CLD-001 to 006 |

**Why Phase 0 is a quick win:** it needs only the Python standard library, so it runs unchanged on Debian, Termux and Windows, and it delivers the one outcome that matters: a payload in, a safe decision and a log line out. It also fixes the hard rules early (fail-closed, size limits, no secrets in logs, offline test), which are expensive to add later. libinjection, PyOD, sqlite-vec and the AI are all optional improvements on top.

## 6. Decisions needed

I list these and do not decide them. Defaults in brackets.

- **D1.** Is the air-gapped directive the governing spec, or is the cloud plan? [directive]. The repo's CLAUDE.md and code currently allow hosted providers and a Supabase backend, which conflicts; D1 settles which wins for this product.
- **D2.** Application language: Python 3.10+ (directive) or the JavaScript Worker (plan)? [Python]
- **D3.** Product name: the `warnetech_` prefix, or "WarNet Control Plane"? [`warnetech_`]
- **D4.** Process: the reference plan contains a live Supabase secret key in plain text. Rotating it is a blocking action (SEC-004). The key must not be copied into any file.
- **D5 (added).** New modules as top-level `warnetech_*` packages, or as subpackages of `warnetech_core`? (PKG-003)
- **D6 (added).** One corrupt line in `signatures.jsonl`: fail the whole file (as DEF-010 proposes) or skip the line and continue? Skipping lets an attacker or a bad write quietly weaken protection.
- **D7 (added).** What feeds the behavioral score (DEF-009), what the minted pattern is built from (DEF-012), and what the AI does in the defense path (AI-006).

## 7. Risks and open questions

- **Missing source plan.** Without the Warnet Control Plan, the old engine's schema, signatures and weighting are `UNKNOWN`; DEF-003 and DEF-007 cannot claim compatibility until samples exist.
- **Dependencies unverified.** libinjection bindings, PyOD (with numpy, scipy and scikit-learn) and sqlite-vec are unchecked for current versions, maintenance, security issues and Python 3.10 support. PyOD is heavy; on Termux it may be impractical (build time, memory). This conflicts with "zero required dependencies" unless they stay optional extras.
- **sqlite-vec feasibility.** Many Python builds ship SQLite without loadable extensions; availability on Termux, Windows and Debian system Python is unknown. MEM-006 keeps keyword retrieval as the fallback.
- **ReDoS.** Python's `re` has no timeout. The plan limits pattern length and input size (DEF-005, SEC-001). Whether that is enough, or a different regex engine is needed, is open and must be tested with hostile patterns.
- **Minting from payloads.** Learning a signature from attack text can store attacker-controlled or sensitive text, and an attacker could poison the set with crafted false-positive reports. Needs D7 and an authenticity rule for who may call `outcome`.
- **Fail-closed availability.** A missing or corrupt signature file blocks everything. That is the safe default for a security gate but an outage for the caller; the runbook (DOC-003) must cover recovery.
- **Thresholds are proposals.** The 0.60 cutoff, the 0.70 seed weight and the 0.85 libinjection floor are my choices to give testable numbers, not values from the directive; the golden set (TST-005) should be used to tune them.
- **Existing system conflicts.** The current repo's Ollama support accepts LAN hosts; the existing memory feature uses SQLite not `rag_store.jsonl`; CI has never run on macOS.
- **"Three CLIs", "same schema the old engine used", and the Section C plan** are referenced by the directive but not defined in anything supplied.
- **Secret exposure.** Until SEC-004 is complete, the leaked Supabase key is a live risk regardless of any local design.

## UNKNOWN items (collected)

1. The "Warnet Control Plane" plan (not supplied).
2. The old engine's signature schema, seed signatures and weighting.
3. Contents of `docs/WARNETECH-ALIGNMENT.md` (not read).
4. Whether the existing `RAGStore` tolerates corrupt lines.
5. Whether libinjection's XSS check returns a fingerprint.
6. Package names, current versions, maintenance status and vulnerabilities of the libinjection binding, PyOD, sqlite-vec, and a type checker.
7. Python 3.10 compatibility of those packages; loadable-extension support on Termux, Windows and Debian system Python.
8. What "the three CLIs" are.
9. Whether the leaked Supabase key exists anywhere outside the supplied plan.
10. Editor or tooling type-check configuration outside the repo.
