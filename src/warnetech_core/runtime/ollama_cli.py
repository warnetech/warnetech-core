"""Wrapper around the ``ollama`` command-line tool.

Used for management: listing installed models, checking what is loaded, and
pulling new ones. Model inference does not go through here; it goes through
the gateway and :class:`~warnetech_core.providers.ollama.OllamaProvider`.

Every command is run as a fixed argv list with no shell. Model names are
passed as single arguments, so a name containing shell metacharacters cannot
inject a command. Names are still validated to a conservative character set.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from ..errors import ConfigError

# Ollama names look like ``llama3.1:8b`` or ``registry.example/ns/model:tag``.
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._/:@-]{1,200}$")
_SIZE_RE = re.compile(r"^([\d.]+)\s*(KB|MB|GB|TB)$", re.IGNORECASE)
_SIZE_TO_GB = {"KB": 1 / (1024 * 1024), "MB": 1 / 1024, "GB": 1.0, "TB": 1024.0}

Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class OllamaModel:
    name: str
    size_gb: float | None
    modified: str = ""


def validate_model_name(name: str) -> str:
    """Return ``name`` if it is a plausible Ollama model name, else raise."""
    if not _MODEL_NAME_RE.fullmatch(name):
        raise ConfigError(f"invalid model name: {name!r}")
    return name


def parse_size(text: str) -> float | None:
    """Convert Ollama's human size (``4.7 GB``) to GiB."""
    match = _SIZE_RE.match(text.strip())
    if not match:
        return None
    return round(float(match.group(1)) * _SIZE_TO_GB[match.group(2).upper()], 3)


def parse_ollama_list(output: str) -> list[OllamaModel]:
    """Parse the table printed by ``ollama list``.

    Columns are ``NAME  ID  SIZE  MODIFIED``. The size spans two words
    (``4.7 GB``), so rows are split on runs of two or more spaces.
    """
    models: list[OllamaModel] = []
    for line in output.splitlines()[1:]:  # first line is the header
        if not line.strip():
            continue
        cols = [c.strip() for c in re.split(r"\s{2,}", line.strip())]
        if len(cols) < 3:
            continue
        name, size_text = cols[0], cols[2]
        modified = cols[3] if len(cols) > 3 else ""
        models.append(OllamaModel(name=name, size_gb=parse_size(size_text), modified=modified))
    return models


def parse_ollama_ps(output: str) -> list[str]:
    """Names of models currently loaded in memory (``ollama ps``)."""
    names: list[str] = []
    for line in output.splitlines()[1:]:
        if line.strip():
            names.append(line.split()[0])
    return names


def _default_runner(argv: list[str], timeout: float) -> str:
    completed = subprocess.run(  # noqa: S603 - argv is built from fixed verbs
        argv, capture_output=True, text=True, timeout=timeout, check=True
    )
    return completed.stdout


class OllamaCLI:
    """Runs ``ollama`` subcommands. The runner is injectable for tests."""

    def __init__(
        self,
        binary: str | None = None,
        *,
        run: Runner | None = None,
        pull_timeout: float = 3600.0,
    ) -> None:
        self._binary = binary
        self._run = run
        self._pull_timeout = pull_timeout

    @property
    def binary(self) -> str:
        if self._binary is None:
            found = shutil.which("ollama")
            if found is None:
                raise ConfigError("the `ollama` command was not found on PATH")
            self._binary = found
        return self._binary

    def _exec(self, args: list[str], timeout: float = 30.0) -> str:
        argv = [self.binary, *args]
        if self._run is not None:
            return self._run(argv)
        return _default_runner(argv, timeout)

    def list_models(self) -> list[OllamaModel]:
        return parse_ollama_list(self._exec(["list"]))

    def loaded_models(self) -> list[str]:
        return parse_ollama_ps(self._exec(["ps"]))

    def pull(self, name: str) -> None:
        self._exec(["pull", validate_model_name(name)], timeout=self._pull_timeout)

    def show(self, name: str) -> str:
        return self._exec(["show", validate_model_name(name)])
