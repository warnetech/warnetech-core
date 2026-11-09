# AGI capabilities

`warnetech_core.agi` is a set of composable parts that work toward a goal:
plan it, reason about each task, call tools, check the result, and retry or
stop. Every model call goes through `AIGateway.complete()`, so budgets, usage
accounting, fallbacks and the offline mode apply.

## Run a goal from the command line

```bash
warnetech agi run "Summarise the open issues in this repo into three themes"
warnetech agi run "Draft a release note" --max-cycles 10
```

Each task prints one line with its outcome (`completed`, `retry` or `failed`),
then a summary. Exit code `0` means every task completed; `1` means the run
stopped short.

With `WARNETECH_OFFLINE=1` the echo model answers instead of a real one. It does
not return a JSON plan, so the run ends at once with `Planner produced no tasks`.
That is expected: it proves the wiring works without keys, not the reasoning.

## Run a goal from Python

```python
import asyncio
from warnetech_core import AIGateway
from warnetech_core.agi import AGIOrchestrator, Goal

async def main():
    gateway = AIGateway.offline()  # or Settings.from_env().build_gateway()
    orchestrator = AGIOrchestrator(gateway, max_cycles=20, max_retries_per_task=2)
    result = await orchestrator.run(Goal(
        description="Write a one-page summary of the release",
        success_criteria=["Covers the top three changes", "Under 300 words"],
    ))
    print(result.success, result.summary)
    for cycle in result.cycles:
        print(cycle.cycle, cycle.task_title, cycle.outcome)

asyncio.run(main())
```

`AGIOrchestrator(gateway, memory=None, *, tool_registry=None, model="chat.default",
project="agi", max_cycles=20, max_retries_per_task=2, telemetry=None,
capabilities=None)`. Pass a `MemoryService` to give each task recent context and
to store each completed result.

## Results

`OrchestratorResult` fields:

| Field | Meaning |
|---|---|
| `goal`, `plan`, `cycles` | As before. `cycles` holds one `CycleRecord` per attempt. |
| `success`, `summary`, `total_ms` | As before. Summary text is unchanged. |
| `confidence` | Heuristic in [0, 1], or `None` if no run started (the planner gave no tasks). |

Confidence starts at 0.50 and adds 0.05 per evidence item (up to +0.35), then
subtracts 0.10 per contradiction. Evidence is successful tool results plus
completed tasks whose reflection had no critiques. Contradictions are critiques
with severity `error` or `critical`, counted across every cycle. The score is
computed once, at the end of the run. It is a heuristic, not a calibrated
probability.

## Telemetry

Telemetry is off unless you pass a sink:

```python
from pathlib import Path
from warnetech_core.agi import AGIOrchestrator
from warnetech_core.agi.telemetry import Telemetry

orchestrator = AGIOrchestrator(gateway, telemetry=Telemetry(Path("agi-events.jsonl")))
```

Each line is one JSON object with `goal_id`, `event`, `timestamp` (UTC) and
`payload`. Events are emitted in this order for a run:

| Event | When | Payload |
|---|---|---|
| `goal_received` | Start of run | `success_criteria` (count) |
| `plan_generated` | After planning | `task_count` |
| `tool_selected` | A `tool_hint` resolved to a registered tool | `cycle`, `task_id`, `tool_name`. With `enable_metric_selection=True` also `hinted_tool`, `candidate_tools` and `selection_reason` (`hint_default`, `single_candidate`, `metric_ranked`). `tool_name` is the tool that executes. |
| `persistence_failed` | An opt-in Phase 2 write failed. The run continues | `goal_id`, `component`, `error_type` |
| `reflection_completed` | After each cycle's reflection | `cycle`, `task_id`, `critiques`, `should_retry`, `has_errors` |
| `execution_completed` | End of run, including the empty-plan case | `success`, `cycles`, `confidence` |

Payloads carry counts and ids, not goal text or model output. Write failures are
logged and swallowed, so a broken sink never fails a run.

## Execution flow

```text
Goal ─► Planning ─► (per ready task)
                     ├─ Memory context (optional)
                     ├─ Reasoning
                     ├─ Tool execution (only if tool_hint resolves)
                     ├─ Reflection
                     └─ Retry / fail / complete
                   ─► Confidence scoring (once, at run end)
                   ─► Execution summary + telemetry
```

Telemetry events are emitted at the points named in the table above. Capability
metadata is stored on the orchestrator but the loop does not consult it yet.

## How a run works

1. **Plan.** The planner asks the model for a JSON list of tasks, each with a
   title, description, optional `depends_on` (titles), optional `tool_hint`.
   Invalid entries are dropped; the rest still form a valid plan. No usable
   tasks means the run ends with `success=False`.
2. **Work each ready task**, one at a time, in dependency order:
   - Look up memory context (if a memory service is given).
   - Reason about the task (chain of thought).
   - If the task's `tool_hint` names a registered tool, run it and append its
     output to the reasoning conclusion.
   - Reflect on the output against the success criteria.
3. **Decide.** A low score or `should_retry` sends the task back, up to
   `max_retries_per_task`. Tool selection runs once per task. A retry reuses
   that selected tool, even if the failed attempt changed the metrics. A retry
   never becomes a different tool, so there is no automatic failover. After that, a critical or error-level critique fails
   the task; otherwise it completes with the reflector's revised output if it
   gave one.
4. **Stop** when every task is complete, a task fails (its dependents never
   run), or `max_cycles` attempts are used up. `max_cycles` counts retries too.

An unreadable reflection counts as a low score (0.5), so it triggers a retry
rather than passing silently.

## Tools

```python
from warnetech_core.agi import ToolDefinition, ToolRegistry

async def search_docs(task: str, description: str) -> str:
    return f"matches for {task!r}"  # call your real code here

registry = ToolRegistry()
registry.register(
    ToolDefinition(name="search_docs", description="Search the docs", tags=["read"]),
    handler=search_docs,
)
orchestrator = AGIOrchestrator(gateway, tool_registry=registry)
```

A tool runs only when a planned task names it in `tool_hint`, or, with metric selection on, when it is an approved candidate in the same `selection_group` as the hinted tool. The executed tool, not the hint, is the tool that receives the call. It receives the task's `task` and `description` as keyword arguments. Errors are caught and
reported on the result; they do not stop the run.

## Supporting modules

| Module | Use it for | Calls a model? |
|---|---|---|
| `ContextWindow` | Fitting a system prompt, RAG chunks and chat history into a token budget. The query is always kept. | No |
| `AgentOrchestrator` | A fixed sequence of async handlers sharing one state dict. A failing handler stops the run and records the error. | No |
| `TemporalEngine` | Hooks that run on every `step()` over carried-over state. | No |

`AgentOrchestrator` and `TemporalEngine` are plain pipelines. `AGIOrchestrator`
covers the same ground for goal-driven work, so use these two only for
model-free pipelines.

## Limits

- Quality depends on the model returning the requested JSON. The parsers
  tolerate malformed output, but a weak model will produce weak plans.
- The loop runs tasks one at a time. It does not parallelise independent tasks.
- Memory is optional. Without it, each task starts with no prior context.
- Nothing runs unless it is registered. The orchestrator has no built-in tools.

## Tests

```bash
pytest tests/test_agi_*.py
```

All AGI tests run offline with scripted gateways. `test_agi_hardening.py` covers
the malformed-output cases and the retry and failure paths. `test_agi_integration.py`
covers the no-telemetry baseline, telemetry event order, tool selection, and the
confidence values for known runs.
