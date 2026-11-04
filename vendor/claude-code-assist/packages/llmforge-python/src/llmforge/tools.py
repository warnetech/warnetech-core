"""Tool definition, schema generation, and gated dispatch.

Three things this gives you that a bare list of dicts does not:

1. **Schemas from signatures.** ``@tool`` reads type hints and the docstring's
   ``Args:`` block, so the schema cannot drift from the implementation.
2. **A policy seam.** Every call passes through a :class:`ToolPolicy` before it
   runs. That is where approval gates, dry-run modes and audit logs live -- and
   it is the reason to promote an action out of a generic ``bash`` tool.
3. **Errors that stay in the loop.** A raising tool becomes a ``tool_result``
   with ``is_error: True`` instead of killing the run, so Claude can recover.
"""

from __future__ import annotations

import inspect
import json
import re
import types as _pytypes
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, get_args, get_origin

from .types import ToolCall, ToolResult

_ARGS_BLOCK = re.compile(r"^\s*(Args|Arguments|Parameters)\s*:\s*$", re.MULTILINE)
_ARG_LINE = re.compile(r"^\s*(\*{0,2}\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)$")

_PRIMITIVES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def json_schema_for(annotation: Any) -> dict[str, Any]:
    """Best-effort JSON Schema for a type annotation.

    Handles primitives, ``list[T]``, ``dict[str, T]``, ``Literal[...]``,
    ``Optional[T]``, and falls back to an unconstrained value for anything
    exotic. An unconstrained parameter is a worse tool but still a working one.
    """
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {}
    if annotation in _PRIMITIVES:
        return {"type": _PRIMITIVES[annotation]}

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal:
        values = list(args)
        schema: dict[str, Any] = {"enum": values}
        kinds = {type(v) for v in values}
        if len(kinds) == 1 and next(iter(kinds)) in _PRIMITIVES:
            schema["type"] = _PRIMITIVES[next(iter(kinds))]
        return schema

    if origin in (typing.Union, _pytypes.UnionType):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return json_schema_for(non_none[0])
        return {"anyOf": [json_schema_for(a) for a in non_none]}

    if origin in (list, set, tuple):
        item = json_schema_for(args[0]) if args else {}
        return {"type": "array", "items": item}

    if origin is dict:
        value = json_schema_for(args[1]) if len(args) == 2 else {}
        return {"type": "object", "additionalProperties": value or True}

    return {}


def parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """Split a docstring into a summary and per-argument descriptions.

    Understands the Google style (``Args:`` then ``name: description``) because
    that is what the SDK decorators expect and what Claude reads best.
    """
    if not doc:
        return "", {}
    doc = inspect.cleandoc(doc)
    match = _ARGS_BLOCK.search(doc)
    if not match:
        return doc.strip(), {}

    summary = doc[: match.start()].strip()
    descriptions: dict[str, str] = {}
    current: str | None = None
    for line in doc[match.end() :].splitlines():
        if not line.strip():
            continue
        if line.strip().endswith(":") and not _ARG_LINE.match(line):
            break  # a new section (Returns:, Raises:)
        hit = _ARG_LINE.match(line)
        if hit:
            current = hit.group(1).lstrip("*")
            descriptions[current] = hit.group(2).strip()
        elif current:
            descriptions[current] += " " + line.strip()
    return summary, descriptions



def resolve_hints(fn: Callable[..., Any]) -> dict[str, Any]:
    """Resolve a function's type hints, degrading instead of raising.

    ``from __future__ import annotations`` turns every hint into a string, and
    ``get_type_hints`` cannot resolve names that live only in an enclosing
    function's scope -- which a tool defined inside a factory, a closure or a
    test always is. Rather than fail there, retry with the typing namespace
    available and fall back per-parameter, leaving anything still unresolvable
    unconstrained. An unconstrained parameter makes a weaker schema; a raised
    NameError makes no tool at all.
    """
    try:
        return typing.get_type_hints(fn)
    except (NameError, TypeError):
        pass

    namespace: dict[str, Any] = {**vars(typing), **getattr(fn, "__globals__", {})}
    resolved: dict[str, Any] = {}
    for name, annotation in getattr(fn, "__annotations__", {}).items():
        if not isinstance(annotation, str):
            resolved[name] = annotation
            continue
        try:
            resolved[name] = eval(annotation, namespace)  # noqa: S307 - own source only
        except Exception:  # noqa: BLE001 - an unresolvable hint is not fatal
            continue
    return resolved


@dataclass
class Tool:
    """A callable plus the schema Claude sees."""

    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., Any]
    strict: bool = True
    """Emit ``strict: true`` so the API guarantees inputs validate."""
    parallel_safe: bool = False
    """Read-only and side-effect free: the harness may fan these out."""
    destructive: bool = False
    """Hard to reverse. Policies should gate these by default."""
    tags: set[str] = field(default_factory=set)

    def definition(self) -> dict[str, Any]:
        """The dict to put in ``request.tools``."""
        spec: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
        if self.strict:
            spec["strict"] = True
        return spec

    def __call__(self, **kwargs: Any) -> Any:
        return self.fn(**kwargs)


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    parallel_safe: bool = False,
    destructive: bool = False,
    strict: bool = True,
    tags: set[str] | None = None,
) -> Any:
    """Turn a typed function into a :class:`Tool`.

    >>> @tool(parallel_safe=True)
    ... def grep(pattern: str, path: str = ".") -> str:
    ...     '''Search files for a regex.
    ...
    ...     Args:
    ...         pattern: The regular expression to search for.
    ...         path: Directory to search under.
    ...     '''
    ...     return "..."
    >>> grep.input_schema["required"]
    ['pattern']
    """

    def wrap(func: Callable[..., Any]) -> Tool:
        summary, arg_docs = parse_docstring(func.__doc__)
        signature = inspect.signature(func)
        hints = resolve_hints(func)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in signature.parameters.items():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            schema = json_schema_for(hints.get(param_name, param.annotation))
            if param_name in arg_docs:
                schema["description"] = arg_docs[param_name]
            properties[param_name] = schema
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        return Tool(
            name=name or func.__name__,
            description=summary or f"Call {func.__name__}.",
            input_schema={
                "type": "object",
                "properties": properties,
                "required": required,
                # Required for `strict: true`, and it stops Claude inventing keys.
                "additionalProperties": False,
            },
            fn=func,
            strict=strict,
            parallel_safe=parallel_safe,
            destructive=destructive,
            tags=set(tags or ()),
        )

    return wrap(fn) if fn is not None else wrap


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Decision:
    """A policy verdict on one pending tool call."""

    allow: bool
    reason: str = ""

    @staticmethod
    def ok() -> Decision:
        return Decision(True)

    @staticmethod
    def deny(reason: str) -> Decision:
        return Decision(False, reason)


class ToolPolicy:
    """Decides whether a tool call may run.

    The default allows everything. Subclass -- or use :class:`GatedPolicy` --
    when a run can touch anything you would not want to explain afterwards.
    """

    def check(self, tool: Tool, call: ToolCall) -> Decision:  # noqa: A002
        return Decision.ok()


class GatedPolicy(ToolPolicy):
    """Denies destructive tools unless explicitly approved.

    ``approve`` receives the tool and the call and returns a bool. In a CLI that
    is a prompt; in a server it is a queued approval; in CI it is ``lambda *_: False``,
    which is exactly the behaviour you want from an unattended run.
    """

    def __init__(
        self,
        approve: Callable[[Tool, ToolCall], bool] | None = None,
        *,
        allow_tags: set[str] | None = None,
        deny_tools: set[str] | None = None,
    ) -> None:
        self.approve = approve or (lambda *_: False)
        self.allow_tags = allow_tags or set()
        self.deny_tools = deny_tools or set()

    def check(self, tool: Tool, call: ToolCall) -> Decision:  # noqa: A002
        if tool.name in self.deny_tools:
            return Decision.deny(f"tool '{tool.name}' is denied by policy")
        if tool.tags & self.allow_tags:
            return Decision.ok()
        if tool.destructive and not self.approve(tool, call):
            return Decision.deny(
                f"'{tool.name}' is destructive and was not approved by the operator"
            )
        return Decision.ok()


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class ToolRegistry:
    """A named set of tools, plus dispatch.

    Order is insertion order and is kept stable on purpose: reordering the tool
    list changes the request prefix and invalidates the prompt cache.
    """

    def __init__(self, *tools: Tool, policy: ToolPolicy | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.add(t)
        self.policy = policy or ToolPolicy()

    def add(self, t: Tool) -> Tool:
        if t.name in self._tools:
            raise ValueError(f"duplicate tool name: {t.name}")
        self._tools[t.name] = t
        return t

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[dict[str, Any]]:
        """Tool definitions for ``request.tools``, in stable order."""
        return [t.definition() for t in self._tools.values()]

    def dispatch(self, call: ToolCall) -> ToolResult:
        """Run one tool call, converting every failure into an error result.

        A tool that raises must not end the run: Claude can often recover from a
        readable error, and a crashed loop loses all the work before it.
        """
        target = self._tools.get(call.name)
        if target is None:
            known = ", ".join(sorted(self._tools)) or "(none)"
            return ToolResult(
                call.id,
                f"Error: no tool named '{call.name}'. Available tools: {known}.",
                is_error=True,
            )

        verdict = self.policy.check(target, call)
        if not verdict.allow:
            return ToolResult(
                call.id,
                f"Error: refused by policy. {verdict.reason}",
                is_error=True,
            )

        try:
            value = target(**call.input)
        except TypeError as exc:
            return ToolResult(call.id, f"Error: bad arguments for '{call.name}': {exc}", True)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model, not swallowed
            return ToolResult(call.id, f"Error: {type(exc).__name__}: {exc}", True)

        return ToolResult(call.id, stringify(value))


def stringify(value: Any) -> str:
    """Coerce a tool return value into text for a ``tool_result`` block."""
    if isinstance(value, str):
        return value
    if value is None:
        return "(no output)"
    try:
        return json.dumps(value, indent=2, default=str)
    except (TypeError, ValueError):  # pragma: no cover - exotic objects
        return str(value)
