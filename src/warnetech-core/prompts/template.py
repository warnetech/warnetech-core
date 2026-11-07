"""Versioned prompt templates.

Prompts are data, not string literals buried in application code. Each template
is identified by ``(id, version)``, carries a content hash so a logged call can
be traced back to the exact text that produced it, and is rendered strictly —
a typo'd variable is an error at render time, not a ``{{ documnet }}`` that
silently ships to the model.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from ..errors import PromptRenderError
from ..types import Message, Role

# ``{{ name }}`` with optional surrounding whitespace.
_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class PromptTemplate:
    """One immutable version of a prompt.

    Attributes:
        id: Stable identifier, shared across versions (``"summarise.document"``).
        version: Monotonic integer. Never edit a published version in place —
            add a new one, so old logs stay interpretable.
        messages: Ordered ``(role, content)`` pairs with ``{{ variable }}`` slots.
        model: Optional default model alias for this prompt.
        variables: Declared variable names. Inferred from the body when omitted;
            declaring them explicitly turns a drifted template into a load-time
            error instead of a runtime one.
    """

    id: str
    version: int
    messages: tuple[tuple[Role, str], ...]
    description: str = ""
    model: str | None = None
    variables: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError(f"prompt {self.id!r}: version must be >= 1")
        if not self.messages:
            raise ValueError(f"prompt {self.id!r}: needs at least one message")
        found = self.discovered_variables()
        if self.variables:
            declared = set(self.variables)
            if declared != found:
                missing = ", ".join(sorted(found - declared)) or "-"
                unused = ", ".join(sorted(declared - found)) or "-"
                raise ValueError(
                    f"prompt {self.id!r} v{self.version}: declared variables do not match "
                    f"the template (undeclared: {missing}; declared but unused: {unused})"
                )
        else:
            object.__setattr__(self, "variables", tuple(sorted(found)))

    def discovered_variables(self) -> set[str]:
        """Variable names actually referenced in the message bodies."""
        return {m.group(1) for _, content in self.messages for m in _VAR_RE.finditer(content)}

    @property
    def checksum(self) -> str:
        """Short content hash. Log this alongside ``(id, version)``."""
        payload = "\n".join(f"{role}:{content}" for role, content in self.messages)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    @property
    def ref(self) -> str:
        """Human-readable reference, e.g. ``summarise.document@v2``."""
        return f"{self.id}@v{self.version}"

    def render(
        self, variables: dict[str, Any] | None = None, *, strict: bool = True
    ) -> list[Message]:
        """Substitute variables and return renderable messages.

        Args:
            variables: Values to substitute.
            strict: When true (the default), missing *and* unexpected variables
                both raise. Turn it off only for exploratory work.

        Raises:
            PromptRenderError: on a missing or unexpected variable.
        """
        values = dict(variables or {})
        required = set(self.variables)
        supplied = set(values)

        if strict:
            missing = required - supplied
            unexpected = supplied - required
            problems = []
            if missing:
                problems.append(f"missing: {', '.join(sorted(missing))}")
            if unexpected:
                problems.append(f"unexpected: {', '.join(sorted(unexpected))}")
            if problems:
                raise PromptRenderError(f"{self.ref} — {'; '.join(problems)}")

        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in values:
                if strict:  # pragma: no cover - guarded by the check above
                    raise PromptRenderError(f"{self.ref} — missing variable {name!r}")
                return match.group(0)
            return str(values[name])

        return [
            Message(role=role, content=_VAR_RE.sub(substitute, content))
            for role, content in self.messages
        ]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptTemplate:
        """Build a template from a parsed YAML/JSON document."""
        try:
            raw_messages = data["messages"]
            prompt_id = data["id"]
        except KeyError as exc:
            raise ValueError(f"prompt document is missing required key {exc.args[0]!r}") from None

        messages: list[tuple[Role, str]] = []
        for entry in raw_messages:
            if not isinstance(entry, dict) or "role" not in entry or "content" not in entry:
                raise ValueError(f"prompt {prompt_id!r}: each message needs `role` and `content`")
            role = entry["role"]
            if role not in ("system", "user", "assistant"):
                raise ValueError(f"prompt {prompt_id!r}: unknown role {role!r}")
            messages.append((role, str(entry["content"])))

        return cls(
            id=str(prompt_id),
            version=int(data.get("version", 1)),
            messages=tuple(messages),
            description=str(data.get("description", "")),
            model=data.get("model"),
            variables=tuple(data.get("variables") or ()),
            tags=tuple(data.get("tags") or ()),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "description": self.description,
            "model": self.model,
            "variables": list(self.variables),
            "tags": list(self.tags),
            "checksum": self.checksum,
            "messages": [{"role": r, "content": c} for r, c in self.messages],
            "metadata": self.metadata,
        }
