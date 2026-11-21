---
status: draft
branch: ccr-2ddd16bf-wsxi86
baseline: 907 passed, 2 skipped; ruff clean (2026-11-09)
---

# AGI Phase 1: capability registry, confidence, telemetry, integration

## Goal

Make every orchestrator run report what it did and how sure it is, without changing
existing behaviour. Phase 1 adds three small modules under `src/warnetech_core/agi/`
and wires them into `AGIOrchestrator` as optional collaborators.

Done means: every run can return `{result, confidence}`, a telemetry JSONL trail exists
for the run when telemetry is enabled, planner/tool metadata is queryable, and all
existing tests still pass.

## Verified starting point

- `agi/tools.py` holds `ToolRegistry` and `ToolDefinition`. There is no `tool_registry.py`.
  `ToolDefinition` has no `version` or `dependencies`. `list_tools` filters by tags.
- `planning.Goal` has no `id`. `Plan` has no `goal_id`.
- No telemetry exists in `agi/`. `CycleRecord` is a per-run trace that is returned, not emitted.
- Confidence signals exist but are unaggregated: `ReflectionResult.overall_quality`,
  `Critique.confidence`, and `ThoughtStep.confidence`. The last two are model self-reports.
- `AGIOrchestrator` calls the reasoner and reflector through `gateway.complete()`.
  Reflection critiques are not passed back to the reasoner on retry (`orchestrator.py`
  lines 135-139 and 163-167). That is a Phase 2 item.
- Bug, reproduced: duplicate task titles make `depends_on` resolve to the last duplicate
  only (`planning._parse_tasks`, `title_to_id` overwrites).
- `AgentOrchestrator`, `TemporalEngine` and `ContextWindow` are not used by
  `AGIOrchestrator`. Their overlap is recorded below and not changed in Phase 1.
- Coverage is not measured. `pytest-cov` is not installed.

## Scope

In:
1. Fix duplicate-title dependency resolution in the planner.
2. Capability metadata: `version`, `dependencies`, and `list_capabilities` / `get_capability`.
3. `ConfidenceScorer` with an explicit evidence and contradiction mapping.
4. `Telemetry` JSONL sink with the five events.
5. Optional wiring into `AGIOrchestrator`, with unchanged defaults.
6. Tests for each, plus a short `docs/AGI.md` update.

Out (Phase 2 or later):
- Reflection feedback on retry, persistent state, tool scoring, world model, multi-agent.
- `capabilities/*.json` files. Metadata lives in code, one source of truth.
- Removing or merging `AgentOrchestrator`, `TemporalEngine` or `ContextWindow`.
- Any change to the `agi run` CLI output format.

## Decisions needed before build

These block the design, so each has a recommendation.

**D1. Where capability metadata lives.** `ToolRegistry` only holds callables with handlers.
The success criterion `get_capability("planner")` needs internal components, which have no
handler, to be registered.
- A (recommended): keep `ToolRegistry` for tools and add `version` / `dependencies` to
  `ToolDefinition`. Add a separate `CapabilityRegistry` of metadata-only records for
  `planner`, `reasoner`, `reflector`, `orchestrator`. This reverses my earlier advice to
  extend only `ToolDefinition`, because internal components are not tools.
- B: one registry with handler optional. Changes `ToolRegistry.execute` semantics. Not recommended.

**D2. Telemetry on or off.** Recommend opt-in: `AGIOrchestrator(telemetry=None)` is a no-op.
The CLI can enable it later with a flag.

**D3. Goal text in telemetry.** Recommend recording `goal_id` and counts only, not the goal
description, because goals can contain user data.

**D4. Confidence inputs.** Proposed mapping:
- `evidence_count` = successful `ToolResult`s in the run plus tasks that completed with no critiques.
- `contradictions` = critiques with severity `error` or `critical` across the run.
- `timeline_match` is dropped from Phase 1. Its default of `True` inflates scores.
- Model self-reported confidences are excluded: uncalibrated.

**D5. Event name.** The brief uses both `execution_complete` and `execution_completed`.
Recommend `execution_completed`.

## Tasks

Each task lists its acceptance check. Run the checks before moving to the next task.

**T1. Fix duplicate-title dependencies** (`planning.py`, `test_agi_planning.py`)
- Map dependencies by index, or give each raw entry a unique key, not by title.
- Test: two tasks titled "Fetch" with a dependent "Report" depends on the correct one.
- Check: `pytest tests/test_agi_planning.py tests/test_agi_hardening.py`.

**T2. Planning `Goal.id`** (`planning.py`)
- Add `id: str = Field(default_factory=...)`. Additive, existing callers unaffected.
- Test: two goals with the same description get different ids.

**T3. Capability metadata** (`tools.py`, `capability_registry.py`, `test_agi_capability_registry.py`)
- Add `version: str = "1.0.0"` and `dependencies: list[str] = []` to `ToolDefinition`.
- Add `CapabilityRegistry` per D1: `register`, `get`, `list_capabilities`, `exists`.
  Reject duplicate names unless `replace=True`.
- Add `validate_dependencies()`: report names that no registry knows, and cycles.
- Built-in records for planner, reasoner, reflector, orchestrator.
- Tests: register, get, list, duplicate rejected, unknown name returns None, missing
  dependency reported, cycle reported.
- Check: `pytest tests/test_agi_capability_registry.py tests/test_agi_tools.py`.

**T4. Confidence engine** (`confidence.py`, `test_agi_confidence.py`)
- `ConfidenceResult` as a Pydantic model, matching the repo.
- `ConfidenceScorer.score(evidence_count: int, contradictions: int) -> ConfidenceResult`.
  Pure function, no gateway calls. Reject negative inputs with `ValueError`.
- Tests with exact values, not `> 0`. Examples: `(0, 0)` gives 0.50; `(5, 0)` gives 0.75;
  `(10, 0)` gives 0.85 (evidence bonus capped at 0.35); `(0, 3)` gives 0.20; `(0, 6)` clamps to
  0.00; `(5, 1)` gives 0.65.
- Check: `pytest tests/test_agi_confidence.py`.

**T5. Telemetry sink** (`telemetry.py`, `test_agi_telemetry.py`)
- `Telemetry(path: Path | None)`. No directory creation at construction. Create the parent
  on first write. Append one JSON object per line.
- `record(event: TelemetryEvent, *, goal_id: str, payload: dict[str, Any] | None = None)`.
- `TelemetryEvent` enum: `goal_received`, `plan_generated`, `tool_selected`,
  `reflection_completed`, `execution_completed`.
- Every record includes UTC timestamp, `goal_id`, event, payload.
- Write failures are logged and swallowed, matching the existing memory-failure pattern.
- Tests use `tmp_path`. Cover: one line per event, parent created on first write, write
  failure does not raise, no file created when no event is recorded.
- Check: `pytest tests/test_agi_telemetry.py`.

**T6. Orchestrator wiring** (`orchestrator.py`, `test_agi_integration.py`)
- Add optional `telemetry: Telemetry | None = None` and `capabilities: CapabilityRegistry | None = None`
  to `AGIOrchestrator.__init__`.
- Add `confidence: float | None = None` to `OrchestratorResult`. Set it at run end from D4.
- Emit `goal_received` at start, `plan_generated` after decompose, `tool_selected` when a
  `tool_hint` resolves, `reflection_completed` per cycle, `execution_completed` at end.
- Keep all existing fields and return values. Summary text unchanged.
- Integration tests with the scripted gateway: (a) without telemetry, the result equals the
  pre-change result on the same script; (b) with telemetry, the expected events appear in
  order; (c) confidence matches the D4 mapping for a known run.
- Check: `pytest tests/test_agi_*.py tests/test_interpreter.py`.

**T7. Docs and final check**
- Update `docs/AGI.md` with the new result fields and telemetry events.
- Final checks, all must pass:
  - `pytest` (full suite): 907 + new tests passing, 2 skipped as before.
  - `ruff check src tests`: clean.
  - `WARNETECH_OFFLINE=1 python examples/quickstart.py`: runs.
  - `warnetech agi run "Analyse operational telemetry"` offline: exits 0 or 1 with a summary, no traceback.

## Risks and rollback

- Confidence numbers will look precise but are heuristic. Label them as such in docs.
- The existing `warnetech agi run` path must not change. T6(a) guards it.
- Rollback: each task is a separate commit. Revert T6 first; T1 through T5 are additive.

## Duplication to resolve later (not Phase 1)

- `AgentOrchestrator` (sequential handler pipeline) overlaps with `AGIOrchestrator` and
  `delegation/`. Decide whether to keep it.
- `TemporalEngine` is a hook runner with no caller in `agi/`. Its `registry` attribute name
  is misleading.
- `ContextWindow` assembles prompts with `f"..."` logging and a `__main__` demo block. Check
  overlap with `chat.py` before reuse.

## Commit plan

Seven commits, one per task, Conventional Commits:
`fix: resolve duplicate task titles in planner dependencies`, `feat: add planning goal ids`,
`feat: add capability metadata and dependency checks`, `feat: add confidence scorer`,
`feat: add opt-in JSONL telemetry`, `feat: wire telemetry and confidence into orchestrator`,
`docs: document AGI phase 1 results and events`.
