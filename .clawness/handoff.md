# Handoff: AGI Phase 1 (not started in code)

Branch: `ccr-2ddd16bf-wsxi86` (pushed, clean at `fbb428a`).
Done: `docs/agi-roadmap.md` committed (`1f41230`), session-tracking commit `fbb428a`.

Next: confirm Phase 1 scope, then build it inside `src/warnetech_core/agi/` (not a root `agi/`).
Scope: `capability_registry.py` wrapping existing `tools.py` ToolRegistry; `confidence.py` scoring planner/reflector output; telemetry events emitted from `planning.py`, `reflection.py`, `orchestrator.py`, `tools.py`; tests in `tests/test_capability_registry.py`, `test_confidence.py`, `test_telemetry.py`, `test_planning.py`.
Run first: `pytest tests/test_agi_planning.py tests/test_agi_tools.py` to confirm the baseline.

Uncommitted: none.

Open questions:
- Confirm Phase 1 scope above before writing code.
- Ghost Alpha reconstruction: real records or synthetic? Still on hold; examples must stay neutral.
- Dependabot alerts were reported on push; not reviewed.
- Attribution: `Co-Authored-By` line omitted from commits (model-identifier rule conflict); confirm.

**Autostart:** no
