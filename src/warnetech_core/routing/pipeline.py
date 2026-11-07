"""Chain several models so each does only the part it is good at.

A pipeline is a list of steps. Each step names a model (an alias, or
``route:<kind>`` to let the router pick) and a prompt template. A template can use
two placeholders, ``{input}`` (the original text) and ``{previous}`` (the last
step's output). Only those exact tokens are replaced, so other braces, such as
JSON in a prompt, are left alone and nothing in the template is evaluated.

A step's output becomes part of the next prompt. That is data from a model, not a
trusted instruction, so keep the templates explicit about what to do with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError
from ..gateway import AIGateway
from .router import TaskRouter

_PLACEHOLDER_RE = re.compile(r"\{(input|previous)\}")
MAX_STEPS = 8
MAX_INPUT_CHARS = 20_000
MAX_CARRY_CHARS = 8_000


@dataclass(frozen=True)
class Step:
    name: str
    model: str  # an alias, or "route:<kind>"
    prompt: str


@dataclass(frozen=True)
class Pipeline:
    name: str
    steps: tuple[Step, ...]


@dataclass
class StepResult:
    name: str
    model: str
    output: str


@dataclass
class PipelineResult:
    steps: list[StepResult] = field(default_factory=list)

    @property
    def final(self) -> str:
        return self.steps[-1].output if self.steps else ""


def load_pipeline(path: str | Path) -> Pipeline:
    file = Path(path)
    try:
        data: Any = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {file}: {exc}") from exc
    raw_steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ConfigError(f"{file.name} needs a non-empty `steps:` list")
    if len(raw_steps) > MAX_STEPS:
        raise ConfigError(f"{file.name} has more than {MAX_STEPS} steps")

    steps: list[Step] = []
    seen: set[str] = set()
    for raw in raw_steps:
        try:
            name, model, prompt = str(raw["name"]), str(raw["model"]), str(raw["prompt"])
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"{file.name}: each step needs name, model and prompt") from exc
        if name in seen:
            raise ConfigError(f"{file.name}: duplicate step name {name!r}")
        seen.add(name)
        steps.append(Step(name, model, prompt))
    return Pipeline(name=str(data.get("name", file.stem)), steps=tuple(steps))


def render(template: str, *, original: str, previous: str) -> str:
    """Fill ``{input}`` and ``{previous}``; leave every other brace untouched.

    Substitution is a single pass over the template, so a placeholder that appears
    inside the substituted text stays literal instead of being filled in again.
    """
    values = {"input": original, "previous": previous}
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


async def run_pipeline(
    gateway: AIGateway, pipeline: Pipeline, text: str, *, router: TaskRouter | None = None
) -> PipelineResult:
    """Run the steps in order, feeding each output into the next."""
    if len(text) > MAX_INPUT_CHARS:
        raise ConfigError(f"input is longer than {MAX_INPUT_CHARS} characters")

    result = PipelineResult()
    previous = ""
    for step in pipeline.steps:
        alias = step.model
        if alias.startswith("route:"):
            if router is None:
                raise ConfigError(f"step {step.name!r} uses {alias!r} but no router was given")
            alias = router.route(text, kind=alias.split(":", 1)[1]).alias
        prompt = render(step.prompt, original=text, previous=previous)
        reply = await gateway.complete(alias, prompt, max_tokens=1024, temperature=0.2)
        result.steps.append(StepResult(step.name, alias, reply.text))
        previous = reply.text[:MAX_CARRY_CHARS]
    return result
