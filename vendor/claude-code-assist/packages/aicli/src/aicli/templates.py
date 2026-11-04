"""Prompt templates and fragments.

A template is a Markdown file with ``{{var}}`` placeholders, ``{{input}}`` for
the prompt or stdin, and ``{{> name}}`` to include a fragment. It may start
with TOML front matter between ``+++`` lines; ``model``, ``system`` and
``max_tokens`` there become defaults for the run, so a template carries
everything it needs to be rerun the same way.

Lookup order: the project's ``.aicli/`` first, then ``~/.aicli/``.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import PROJECT_DIR, ConfigError, home

VAR = re.compile(r"\{\{\s*([A-Za-z_][\w-]*)\s*\}\}")
INCLUDE = re.compile(r"\{\{>\s*([\w./-]+)\s*\}\}")
MAX_FILE_BYTES = 200_000  # context files beyond this are a cost accident


@dataclass
class Template:
    name: str
    body: str
    defaults: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    def variables(self) -> list[str]:
        return sorted(set(VAR.findall(self.body)) - {"input"})


def search_dirs(kind: str, project_root: Path | None) -> list[Path]:
    dirs = []
    if project_root:
        dirs.append(project_root / PROJECT_DIR / kind)
    dirs.append(home() / kind)
    return dirs


def _find(kind: str, name: str, project_root: Path | None) -> Path | None:
    for directory in search_dirs(kind, project_root):
        for candidate in (directory / name, directory / f"{name}.md"):
            if candidate.is_file():
                return candidate
    return None


def parse(name: str, text: str, path: Path | None = None) -> Template:
    defaults: dict[str, Any] = {}
    if text.startswith("+++\n"):
        head, sep, rest = text[4:].partition("\n+++\n")
        if not sep:
            raise ConfigError(f"template {name!r}: front matter opened with +++ but never closed")
        try:
            defaults = tomllib.loads(head)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"template {name!r} front matter: {exc}") from exc
        text = rest
    return Template(name, text, defaults, path)


def load(name: str, project_root: Path | None) -> Template:
    path = _find("templates", name, project_root)
    if path is None:
        where = ", ".join(str(d) for d in search_dirs("templates", project_root))
        raise ConfigError(f"no template {name!r} (looked in {where})")
    return parse(name, path.read_text(encoding="utf-8"), path)


def list_all(project_root: Path | None) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for directory in search_dirs("templates", project_root):
        if directory.is_dir():
            for path in sorted(directory.glob("*.md")):
                found.setdefault(path.stem, path)  # project wins over user
    return found


def fragment(name: str, project_root: Path | None) -> str:
    path = _find("fragments", name, project_root)
    if path is None:
        raise ConfigError(f"no fragment {name!r}")
    return path.read_text(encoding="utf-8")


def render(tpl: Template, variables: dict[str, str], project_root: Path | None,
           depth: int = 0) -> str:
    if depth > 5:
        raise ConfigError(f"template {tpl.name!r}: fragments nested more than 5 deep")

    def include(m: re.Match[str]) -> str:
        inner = parse(m.group(1), fragment(m.group(1), project_root))
        return render(inner, variables, project_root, depth + 1)

    body = INCLUDE.sub(include, tpl.body)
    missing = sorted({v for v in VAR.findall(body) if v not in variables})
    if missing:
        needs = [" ".join(f"--var {m}=..." for m in missing if m != "input")]
        if "input" in missing:
            needs.append("a prompt or stdin")
        raise ConfigError(f"template {tpl.name!r} needs " + " and ".join(n for n in needs if n))
    return VAR.sub(lambda m: variables[m.group(1)], body)


def context_block(spec: str, project_root: Path | None) -> str:
    """``-F`` argument: a file path (wrapped with its name) or a fragment name."""
    path = Path(spec)
    if path.is_file():
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ConfigError(f"{spec} is {size:,} bytes; the limit for -F is {MAX_FILE_BYTES:,} "
                              "(send a smaller slice, e.g. via stdin)")
        text = path.read_text(encoding="utf-8", errors="replace")
        return f'<file path="{spec}">\n{text}\n</file>'
    return fragment(spec, project_root)
