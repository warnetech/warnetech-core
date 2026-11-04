"""The repository's structure, checked rather than described.

CLAUDE.md and the README describe the layout. This script makes that
description a contract: if a folder is renamed, a numbered doc or lab is
skipped, or a package appears that nobody documented, CI fails and says
exactly what to fix. A layout described in prose only drifts.

Stdlib only. Exit 0 = layout matches; exit 1 = every violation is printed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ("pyproject.toml", "package.json")


def numbered(directory: Path, pattern: str, start: int) -> tuple[list[Path], list[str]]:
    """Entries named NN<sep>... must be numbered start, start+1, ... with no gaps or repeats."""
    rx = re.compile(pattern)
    items = sorted(p for p in directory.iterdir() if rx.match(p.name))
    numbers = [int(rx.match(p.name).group(1)) for p in items]
    errors = []
    expected = list(range(start, start + len(items)))
    if numbers != expected:
        errors.append(f"{directory.relative_to(ROOT)}/: numbering is {numbers}, expected {expected}")
    return items, errors


def layout_paths(text: str, heading: str) -> list[str]:
    """First token of each line in the code block under ``heading``."""
    block = text.split(heading, 1)[1].split("```", 2)[1]
    return [line.split()[0] for line in block.splitlines() if line.strip() and line[0] != " "]


def main() -> int:
    errors: list[str] = []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    # 1. Every path in the documented maps exists.
    for source, text, heading in (("README.md", readme, "## Layout"),
                                  ("CLAUDE.md", claude, "## Map")):
        for path in layout_paths(text, heading):
            if not (ROOT / path).exists():
                errors.append(f"{source} map lists {path}, which does not exist")

    # 2. Every package is documented in both maps and has a manifest.
    for pkg in sorted(p for p in (ROOT / "packages").iterdir() if p.is_dir()):
        rel = f"packages/{pkg.name}/"
        for source, text in (("README.md", readme), ("CLAUDE.md", claude)):
            if rel not in text:
                errors.append(f"{rel} is not in {source}")
        if not any((pkg / m).exists() for m in MANIFESTS):
            errors.append(f"{rel} has no {' or '.join(MANIFESTS)}")

    # 3. Numbered sequences: docs from 00, labs from 01, examples from 01.
    docs, e = numbered(ROOT / "docs", r"^(\d\d)-.+\.md$", 0)
    errors += e
    for doc in docs:
        if f"docs/{doc.name}" not in readme:
            errors.append(f"docs/{doc.name} is not linked from the README's Documentation table")
    labs, e = numbered(ROOT / "labs", r"^(\d\d)-.+$", 1)
    errors += e
    lab_index = (ROOT / "labs" / "README.md").read_text(encoding="utf-8")
    for lab in labs:
        if not (lab / "README.md").exists():
            errors.append(f"labs/{lab.name}/ has no README.md")
        if lab.name not in lab_index:
            errors.append(f"labs/{lab.name} is not in labs/README.md")
    _, e = numbered(ROOT / "examples", r"^(\d\d)_.+\.py$", 1)
    errors += e

    # 4. Nothing generated is tracked.
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True).stdout.splitlines()
    junk = re.compile(r"(__pycache__|\.pytest_cache|\.ruff_cache|node_modules|\.egg-info|"
                      r"/dist/|\.db$|\.trace\.jsonl$)")
    errors += [f"generated file is tracked: {f}" for f in tracked if junk.search(f)]

    for err in errors:
        print(f"layout: {err}")
    print(f"layout: {'OK' if not errors else f'{len(errors)} problem(s)'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
