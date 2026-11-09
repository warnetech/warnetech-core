---
status: done
---

# AGI Phase 2 plan

Source: `docs/agi-roadmap.md` (Phase 2 headings) and the placeholder list in its status section.
Numbering confirmed by the user on 2026-11-09.

## Scope (this pass)

| Group | Item | Module | Status |
|---|---|---|---|
| Reflection Engine | Reflection history | `agi/state.py` (`ReflectionHistory`) | build |
| Persistent State | State store | `agi/state.py` (`AgentStateStore`) | build |
| Persistent State | Goal history | `agi/state.py` (`AgentStateStore.record_goal`) | build |
| Tool Selection | Tool metrics | `agi/tool_metrics.py` (`ToolMetrics`) | build |
| Tool Selection | Bayesian ranking | `agi/tool_metrics.py` (`rank`) | build |
| Tool Selection | Adaptive selection | `agi/tool_metrics.py` (`choose`, Thompson sampling) | build |

## Design decisions

- Stdlib only (CLAUDE.md rule 4). JSON files under a caller-supplied root directory, written atomically (temp file + `os.replace`).
- No network, no model calls. Both modules are pure and offline-testable.
- Bayesian ranking uses a Beta(1, 1) prior by default: posterior mean = (successes + a) / (trials + a + b).
- Adaptive selection uses Thompson sampling over the same Beta posteriors. The RNG is injectable so tests are deterministic.
- Nothing is wired into `AGIOrchestrator` yet. Wiring is a separate step, so the Phase 1 run loop and its 954 tests stay untouched.

## Deferred to the wiring step

- Calling `AgentStateStore` and `ToolMetrics` from `AGIOrchestrator` (run loop unchanged in this pass).

## Out of scope (later phases)

- Orchestrator integration of the store and tool selection.
- Beliefs, plans and memories files from roadmap section 4.
- World model, multi-ghost coordination, evaluation harness (Phases 3 and 4).

## Verification

- `pytest tests/test_agi_state.py tests/test_agi_tool_metrics.py`
- `pytest` (full suite, expect 954 + new tests passing)
- `ruff check src tests`
