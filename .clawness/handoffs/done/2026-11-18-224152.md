# Handoff — warnetech-core, branch claude/blissful-fermi-b3mz47

## Done
- Install fixed (entry points, pydantic cap, py3.10+); 955 tests pass on Python 3.12 and 3.13.
- `warnetech-alloc` built: P0-P3 rules, classifier, scan/ingest/audit, `--memory` link, crash recovery (`audit --repair`), dashboard at `/ui/alloc`. Docs: `docs/ALLOC.md`.
- CI: Python 3.13 added and a `wheel-smoke` job (never run on GitHub yet).

## Next
- Owner runs on the ThinkPad: `git fetch origin && git checkout claude/blissful-fermi-b3mz47`, then `py -3.12 -m venv .venv`, `.venv\Scripts\activate`, `pip install -e ".[dev]"`, `pytest -q`; send the last line.

## Open questions
- Step 5 (MCP bridge checks): which servers, and is outbound network allowed?
- `CLAUDE.md` says "zero required runtime dependencies" but httpx, pydantic, pyyaml are required: change the sentence or the deps?
- Release tag 0.1.1: go-ahead?
- Codeflash fails on an expired certificate at app.codeflash.ai; do not disable TLS verification.
- Repo split: not needed now. If ever wanted: defense-engine PRD (`docs/PRD.md`) -> `one-tech-defense`, deferred cloud pieces -> `one-tech-cloud`, `vendor/` -> already separate upstream repos.

**Autostart:** no
