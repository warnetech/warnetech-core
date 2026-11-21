"""Locate and call the repositories vendored under ``vendor/``.

``vendor/vendor.json`` is the source of truth for which repositories are bundled
and which commit each came from. Lookups go through that manifest, so a name that
is not listed is refused rather than joined onto a path.

Everything here works from a source checkout. A wheel install has no ``vendor/``
directory; the helpers then report it as unavailable instead of failing oddly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..errors import ConfigError

_MANIFEST_NAME = "vendor.json"
# The bundled aicli declares requires-python >=3.11 (it imports tomllib).
AICLI_MIN_PYTHON = (3, 11)
# src/warnetech_core/integrations/vendor.py -> repository root is three levels up.
_DEFAULT_ROOT = Path(__file__).resolve().parents[3]


def repo_root() -> Path:
    """The repository root. ``WARNETECH_REPO_ROOT`` overrides it (used by tests)."""
    override = os.getenv("WARNETECH_REPO_ROOT")
    return Path(override).resolve() if override else _DEFAULT_ROOT


def manifest() -> dict[str, dict[str, Any]]:
    """Return the vendored-repository manifest, or ``{}`` if there is none."""
    path = repo_root() / "vendor" / _MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must hold a JSON object")
    return data


def vendor_path(name: str) -> Path:
    """Absolute path of a vendored repository, by its manifest name."""
    entry = manifest().get(name)
    if entry is None:
        known = ", ".join(sorted(manifest())) or "none"
        raise ConfigError(f"unknown vendored repository {name!r} (known: {known})")
    path = (repo_root() / entry["path"]).resolve()
    if not path.is_relative_to(repo_root() / "vendor"):
        raise ConfigError(f"manifest path for {name!r} leaves vendor/: {entry['path']!r}")
    if not path.is_dir():
        raise ConfigError(f"{name!r} is listed but {path} is missing")
    return path


def available() -> list[str]:
    """Names of vendored repositories that are listed and present on disk."""
    names: list[str] = []
    for name in sorted(manifest()):
        try:
            vendor_path(name)
        except ConfigError:
            continue
        names.append(name)
    return names


def aicli_source() -> Path:
    """Directory to put on ``PYTHONPATH`` to import the vendored ``aicli``."""
    src = vendor_path("claude-code-assist") / "packages" / "aicli" / "src"
    if not src.is_dir():
        raise ConfigError(f"aicli source not found at {src}")
    return src


def run_aicli(args: list[str], *, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    """Run the vendored ``aicli`` with ``args`` and return the finished process.

    aicli is a separate product with its own provider layer. It runs in a child
    process, so the parent environment (including any provider keys aicli reads)
    is passed through. Arguments are a list and no shell is used.

    Raises :class:`ConfigError` on Python older than 3.11, which aicli does not support;
    the rest of warnetech_core still runs on 3.10.
    """
    if tuple(sys.version_info[:2]) < AICLI_MIN_PYTHON:
        have = ".".join(str(n) for n in sys.version_info[:2])
        need = ".".join(str(n) for n in AICLI_MIN_PYTHON)
        raise ConfigError(f"the bundled aicli needs Python {need} or newer; this is {have}")
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{aicli_source()}{os.pathsep}{existing}" if existing else str(aicli_source())
    )
    return subprocess.run(  # noqa: S603 - fixed interpreter, list args, no shell
        # -P keeps the working directory off sys.path, so a planted `aicli` package there
        # cannot shadow the vendored one. Python 3.11+ only, which the guard above enforces.
        [sys.executable, "-P", "-m", "aicli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
