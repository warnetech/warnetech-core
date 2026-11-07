"""Prompt registry: many versions per id, loaded from disk or code."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from ..errors import PromptNotFound
from ..types import Message
from .template import PromptTemplate


class PromptRegistry:
    """Holds every known prompt version and resolves ``id`` -> latest."""

    def __init__(self, templates: Iterable[PromptTemplate] = ()) -> None:
        self._by_id: dict[str, dict[int, PromptTemplate]] = {}
        for template in templates:
            self.register(template)

    def register(
        self, template: PromptTemplate, *, allow_overwrite: bool = False
    ) -> PromptTemplate:
        """Add a version.

        Re-registering an existing ``(id, version)`` with *different* content is
        refused: published versions are immutable, because logs reference them.
        """
        versions = self._by_id.setdefault(template.id, {})
        existing = versions.get(template.version)
        if (
            existing is not None
            and not allow_overwrite
            and existing.checksum != template.checksum
        ):
            raise ValueError(
                f"prompt {template.ref} is already registered with different content "
                f"({existing.checksum} != {template.checksum}); bump the version instead"
            )
        versions[template.version] = template
        return template

    def add(
        self,
        prompt_id: str,
        messages: list[tuple[str, str]] | list[dict[str, str]],
        *,
        version: int = 1,
        **kwargs: Any,
    ) -> PromptTemplate:
        """Convenience constructor for defining a prompt in code."""
        normalised = [
            (m["role"], m["content"]) if isinstance(m, dict) else (m[0], m[1]) for m in messages
        ]
        return self.register(
            PromptTemplate(
                id=prompt_id,
                version=version,
                messages=tuple(normalised),  # type: ignore[arg-type]
                **kwargs,
            )
        )

    def get(self, prompt_id: str, version: int | None = None) -> PromptTemplate:
        """Fetch a prompt. ``version=None`` returns the highest version."""
        versions = self._by_id.get(prompt_id)
        if not versions:
            known = ", ".join(sorted(self._by_id)) or "none registered"
            raise PromptNotFound(f"no prompt with id {prompt_id!r} (known: {known})")
        if version is None:
            return versions[max(versions)]
        try:
            return versions[version]
        except KeyError:
            available = ", ".join(f"v{v}" for v in sorted(versions))
            raise PromptNotFound(
                f"prompt {prompt_id!r} has no version {version} (available: {available})"
            ) from None

    def render(
        self,
        prompt_id: str,
        variables: dict[str, Any] | None = None,
        *,
        version: int | None = None,
        strict: bool = True,
    ) -> list[Message]:
        """Fetch and render in one step."""
        return self.get(prompt_id, version).render(variables, strict=strict)

    def versions(self, prompt_id: str) -> list[int]:
        return sorted(self._by_id.get(prompt_id, {}))

    def ids(self) -> list[str]:
        return sorted(self._by_id)

    def all(self) -> list[PromptTemplate]:
        return [t for versions in self._by_id.values() for t in versions.values()]

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_id.values())

    def __contains__(self, prompt_id: object) -> bool:
        return prompt_id in self._by_id

    # ---- loading --------------------------------------------------------

    def load_file(self, path: str | Path) -> list[PromptTemplate]:
        """Load one YAML file holding a single prompt or a list of prompts."""
        path = Path(path)
        data = yaml.safe_load(path.read_text())
        documents = data if isinstance(data, list) else [data]
        loaded = []
        for document in documents:
            if not document:
                continue
            try:
                loaded.append(self.register(PromptTemplate.from_dict(document)))
            except ValueError as exc:
                raise ValueError(f"{path}: {exc}") from exc
        return loaded

    def load_dir(self, path: str | Path, *, pattern: str = "**/*.y*ml") -> list[PromptTemplate]:
        """Load every prompt file under a directory tree."""
        root = Path(path)
        if not root.is_dir():
            raise FileNotFoundError(f"prompt directory not found: {root}")
        loaded = []
        for file in sorted(root.glob(pattern)):
            loaded.extend(self.load_file(file))
        return loaded

    @classmethod
    def from_dir(cls, path: str | Path) -> PromptRegistry:
        registry = cls()
        registry.load_dir(path)
        return registry
