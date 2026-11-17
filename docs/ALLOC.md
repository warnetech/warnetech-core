# warnetech-alloc

Classifies files into priority levels and tiers, then optionally copies them into a tiered layout with an index.

## Commands

```bash
warnetech-alloc scan <path>                 # dry run: predicted priority and tier
warnetech-alloc ingest <path>               # plan only, copies nothing
warnetech-alloc ingest <path> --apply       # copy into the tier folders and index them
warnetech-alloc ingest <path> --apply --memory  # also index into the warnetech memory store
warnetech-alloc audit                       # check index against disk and tier quotas
```

Exit codes: `0` ok, `1` audit found problems, `2` bad usage or rules file.

## Behaviour

- **Ingest copies; it never moves or deletes the source.**
- Content already in the index (same SHA-256) is skipped, so re-running ingest adds nothing.
- With `--memory`, each ingested file is stored in the warnetech memory store (`get_context` can then find it). P0 and P1 text files contribute their opening text. P2 and P3 files, and all binaries, are stored as a header line only.
- Hidden files and folders are skipped.
- The content pass reads only the first `sniff_bytes` (default 4096) of each file. It is keyword and byte based. There is no embedding or LLM pass yet.

## Priorities

| Level | Rule (first match wins) | Tier |
|---|---|---|
| P0 | Filename matches a critical pattern, or the first bytes contain `[CRITICAL]` | 0 hot (pinned) |
| P3 | Transient suffix, duplicate content, binary with no known reader, or old and unknown type | 3 quarantine |
| P1 | Modified within `active_days` (14), or between 14 and 30 days | 1 warm |
| P2 | Inactive beyond `reference_days` (30), known type | 1 warm, eligible for tier 2 |

**Decided:** files between `active_days` and `reference_days` (14 to 30 days old) are P1. The plan left this band undefined.

## Dashboard

```bash
pip install -e ".[service]"
WARNETECH_OFFLINE=1 WARNETECH_ALLOW_ANONYMOUS=1 \
  uvicorn warnetech_core.service.app:app --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080/ui/alloc>. It reads `WARNETECH_ALLOC_DIR` (default `data/alloc`) and the memory store.

- **Storage and allocation monitor:** files and size per tier, quota, recent routing decisions, and an on-demand audit.
- **Priority queue and override console:** files sorted P0 to P3. Choose a new priority and **Apply** to move the stored copy to that tier's folder. The change is appended to the index, so history is kept and the latest record wins. Only copies inside the allocation directory are ever moved; your originals are not touched.
- **Memory inspector:** search the indexed files.

Same protections as the main UI: localhost-only, the service token when set, a strict CSP, no outside requests.

**Known limitation:** a manual override updates the allocation index but not the matching memory record, so the memory inspector may show the old priority until the file is re-indexed.

## Rules file

Copy `config/priority_rules.yaml`, edit, and pass it with `--rules` or `WARNETECH_ALLOC_RULES`. Unknown keys are errors.

## Not in Phase 1

- MCP bridge checks
- MCP bridge status view (Step 5)
- Cloud tunnels, Milvus and Workers (these conflict with the air-gapped rule in `docs/PRD.md`)
- Priority decay and automatic migration to tier 2 (`audit` only reports; it doesn't re-prioritise)
