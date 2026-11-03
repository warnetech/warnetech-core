"""The agent loop.

The SDK ships a tool runner that handles the common case well; reach for it
first. This loop exists for the cases the runner does not expose, and every one
of them shows up in real integrations:

* **Budgets that bind.** Steps, wall-clock, and dollars, checked every turn.
* **``pause_turn`` resumption.** Server tools can pause a turn. The runner
  returns the paused message as if it were final -- a silently truncated answer.
  This loop resumes it, up to a cap.
* **Refusal handling.** ``stop_reason == "refusal"`` returns HTTP 200. Code that
  reads ``content`` without checking gets an empty string and no error.
* **Transcript ownership.** You hold the message list, so you can compact it,
  fork it, checkpoint it, or hand it to a lab.

Everything here is synchronous and boring on purpose. The interesting parts of
an agent are the tools, the context and the evals -- not the ``while``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .tools import ToolRegistry
from .trace import NULL_TRACER, Tracer, cost_usd
from .types import Request, Response, ToolCall, ToolResult, Usage


class BudgetExceeded(RuntimeError):
    """Raised when a run hits a limit you set. Carries the partial transcript."""

    def __init__(self, message: str, run: RunResult):
        super().__init__(message)
        self.run = run


@dataclass(slots=True)
class Budget:
    """Hard limits on a single run.

    Defaults are deliberately finite. An agent with no ceiling is an outage
    waiting for the right prompt.
    """

    max_steps: int = 20
    max_seconds: float = 600.0
    max_usd: float = 5.00
    max_pause_resumes: int = 5
    on_exceeded: str = "raise"
    """``"raise"`` or ``"stop"`` -- stop returns the partial run instead."""

    def within(self, ceiling: Budget) -> Budget:
        """Validate this budget against an operator-set ceiling.

        **Refuses, never clamps.** Silently lowering a budget to fit means a
        run that was configured to do a large job quietly does a fraction of it
        and reports success -- the failure is invisible at exactly the moment
        someone was relying on the number they set. A misconfigured budget
        should fail loudly at startup instead.

        Borrowed from the container-profile check in
        tewartech-node/claude-command-cli, which makes the same call for memory
        and CPU ceilings: *"a profile that requests more than the configured
        maximum is refused, not clamped, so a misconfigured scenario fails
        loudly instead of silently running under-isolated."*

        >>> Budget(max_steps=5).within(Budget(max_steps=10)).max_steps
        5
        """
        exceeded = [
            f"{field}={mine} exceeds the ceiling of {theirs}"
            for field, mine, theirs in (
                ("max_steps", self.max_steps, ceiling.max_steps),
                ("max_seconds", self.max_seconds, ceiling.max_seconds),
                ("max_usd", self.max_usd, ceiling.max_usd),
                ("max_pause_resumes", self.max_pause_resumes, ceiling.max_pause_resumes),
            )
            if mine > theirs
        ]
        if exceeded:
            raise ValueError(
                "budget exceeds the configured ceiling: " + "; ".join(exceeded)
            )
        return self


@dataclass(slots=True)
class Step:
    """One turn: a model call plus whatever tools it triggered."""

    index: int
    response: Response
    results: list[ToolResult] = field(default_factory=list)
    seconds: float = 0.0


@dataclass(slots=True)
class RunResult:
    """The outcome of :meth:`Agent.run`."""

    text: str
    messages: list[dict[str, Any]]
    steps: list[Step] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    stop_reason: str | None = None
    halted: str | None = None
    """Set when a budget stopped the run early."""

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [c for s in self.steps for c in s.response.tool_calls]

    @property
    def tool_errors(self) -> list[ToolResult]:
        return [r for s in self.steps for r in s.results if r.is_error]

    def __bool__(self) -> bool:
        return self.halted is None and self.stop_reason not in ("refusal", "max_tokens")


Hook = Callable[["Agent", Step], None]


class Agent:
    """A model, a tool registry, and a bounded loop over the two.

    >>> from llmforge.provider import FakeProvider
    >>> agent = Agent(FakeProvider(["done"]))
    >>> agent.run("say something").text
    'done'
    """

    def __init__(
        self,
        provider: Any,
        *,
        tools: ToolRegistry | None = None,
        system: str | list[dict[str, Any]] | None = None,
        model: str = "claude-opus-5",
        max_tokens: int = 16000,
        effort: str | None = None,
        thinking: dict[str, Any] | None = None,
        budget: Budget | None = None,
        tracer: Tracer | None = None,
        cache: bool = True,
        on_step: Hook | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools or ToolRegistry()
        self.system = system
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.thinking = thinking
        self.budget = budget or Budget()
        self.tracer = tracer or NULL_TRACER
        self.cache = cache
        self.on_step = on_step

    # -- request construction ----------------------------------------------- #

    def _request(self, messages: list[dict[str, Any]], **overrides: Any) -> Request:
        request = Request(
            messages=messages,
            model=self.model,
            system=self.system,
            tools=self.tools.definitions(),
            max_tokens=self.max_tokens,
            effort=self.effort,  # type: ignore[arg-type]
            thinking=self.thinking,
            cache=self.cache,
        )
        return request.with_(**overrides) if overrides else request

    # -- the loop ------------------------------------------------------------ #

    def run(
        self,
        prompt: str | list[dict[str, Any]],
        *,
        messages: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> RunResult:
        """Drive the loop until the model stops asking for tools.

        ``prompt`` may be a string or a content-block list. Pass ``messages`` to
        continue an existing transcript -- it is copied, never mutated in place.
        """
        transcript: list[dict[str, Any]] = list(messages or [])
        transcript.append({"role": "user", "content": prompt})

        run = RunResult(text="", messages=transcript)
        started = time.time()
        pause_resumes = 0

        with self.tracer.span("agent.run", kind="run", model=self.model):
            while True:
                halt = self._check_budget(run, started)
                if halt:
                    return self._halt(run, halt)

                step_started = time.time()
                response = self.provider.complete(self._request(transcript, **overrides))

                run.usage = run.usage + response.usage
                run.cost_usd += cost_usd(response.model or self.model, response.usage)
                run.stop_reason = response.stop_reason
                step = Step(index=len(run.steps), response=response)
                run.steps.append(step)

                # A refusal is an HTTP 200 with empty-looking content. Stop here
                # rather than looping on a response that will never carry tools.
                if response.stop_reason == "refusal":
                    run.text = response.text
                    run.halted = f"refusal:{response.refusal_category or 'unspecified'}"
                    step.seconds = time.time() - step_started
                    self._fire(step)
                    return run

                transcript.append({"role": "assistant", "content": response.content or response.text})

                # A server tool paused mid-turn. Re-send to continue; the paused
                # assistant turn is already the last entry.
                if response.stop_reason == "pause_turn":
                    pause_resumes += 1
                    if pause_resumes > self.budget.max_pause_resumes:
                        run.text = response.text
                        return self._halt(run, "max_pause_resumes")
                    step.seconds = time.time() - step_started
                    self._fire(step)
                    continue

                if not response.tool_calls:
                    run.text = response.text
                    step.seconds = time.time() - step_started
                    self._fire(step)
                    return run

                # Every tool_use block needs a matching tool_result, and they all
                # go back in ONE user message -- splitting them across messages
                # teaches the model to stop calling tools in parallel.
                results = [self._dispatch(call) for call in response.tool_calls]
                step.results = results
                transcript.append({"role": "user", "content": [r.to_block() for r in results]})

                step.seconds = time.time() - step_started
                self._fire(step)

    # -- helpers ------------------------------------------------------------- #

    def _dispatch(self, call: ToolCall) -> ToolResult:
        with self.tracer.span(f"tool.{call.name}", kind="tool", tool=call.name) as span:
            result = self.tools.dispatch(call)
            span.attrs["is_error"] = result.is_error
            span.attrs["result_chars"] = len(result.content)
            return result

    def _fire(self, step: Step) -> None:
        if self.on_step is not None:
            self.on_step(self, step)

    def _check_budget(self, run: RunResult, started: float) -> str | None:
        if len(run.steps) >= self.budget.max_steps:
            return "max_steps"
        if time.time() - started > self.budget.max_seconds:
            return "max_seconds"
        if run.cost_usd > self.budget.max_usd:
            return "max_usd"
        return None

    def _halt(self, run: RunResult, reason: str) -> RunResult:
        run.halted = reason
        if not run.text and run.steps:
            run.text = run.steps[-1].response.text
        if self.budget.on_exceeded == "raise":
            raise BudgetExceeded(
                f"run halted: {reason} "
                f"(steps={len(run.steps)}, cost=${run.cost_usd:.4f})",
                run,
            )
        return run
