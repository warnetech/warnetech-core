# warnetech-alloc

Classifies files into priority levels and tiers, then optionally copies them into a tiered layout with an index.

## Commands

```bash
warnetech-alloc scan <path>                 # dry run: predicted priority and tier
warnetech-alloc ingest <path>               # plan only, copies nothing
warnetech-alloc ingest <path> --apply       # copy into the tier folders and index them
warnetech-alloc audit                       # check index against disk and tier quotas
```

Exit codes: `0` ok, `1` audit found problems, `2` bad usage or rules file.

## Behaviour

- **Ingest copies; it never moves or deletes the source.**
- Content already in the index (same SHA-256) is skipped, so re-running ingest adds nothing.
- Hidden files and folders are skipped.
- The content pass reads only the first `sniff_bytes` (default 4096) of each file. It is keyword and byte based. There is no embedding or LLM pass yet.

## Priorities

| Level | Rule (first match wins) | Tier |
|---|---|---|
| P0 | Filename matches a critical pattern, or the first bytes contain `[CRITICAL]` | 0 hot (pinned) |
| P3 | Transient suffix, duplicate content, binary with no known reader, or old and unknown type | 3 quarantine |
| P1 | Modified within `active_days` (14), or between 14 and 30 days | 1 warm |
| P2 | Inactive beyond `reference_days` (30), known type | 1 warm, eligible for tier 2 |

**Open question:** the plan leaves the 14 to 30 day band undefined. It is currently P1. Change it in the rules file once decided.

## Rules file

Copy `config/priority_rules.yaml`, edit, and pass it with `--rules` or `WARNETECH_ALLOC_RULES`. Unknown keys are errors.

## Not in Phase 1

- Web dashboard (FastAPI and HTMX)
- MCP bridge checks
- Cloud tunnels, Milvus and Workers (these conflict with the air-gapped rule in `docs/PRD.md`)
- Priority decay and automatic migration to tier 2 (`audit` only reports; it doesn't re-prioritise)
- Writing ingested items into the RAG memory index
