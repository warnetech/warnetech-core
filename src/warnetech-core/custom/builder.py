"""Create a custom model in Ollama from a validated spec."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..errors import ConfigError
from ..runtime.ollama_cli import OllamaCLI
from .modelfile import render_modelfile
from .spec import CustomModel


def resolve_adapter(spec: CustomModel, spec_dir: Path) -> Path | None:
    """Return the adapter's absolute path, or ``None`` if the spec has none.

    The spec validator already rejected absolute and ``..`` paths. Resolving here
    also catches a symlink that points outside the spec's directory, and a file
    that does not exist, before anything is handed to Ollama.
    """
    if spec.adapter is None:
        return None
    root = spec_dir.resolve()
    path = (root / spec.adapter).resolve()
    if not path.is_relative_to(root):
        raise ConfigError(f"adapter for {spec.name!r} resolves outside {root}")
    if not path.exists():
        raise ConfigError(f"adapter for {spec.name!r} not found: {path}")
    return path


class CustomModelBuilder:
    def __init__(self, cli: OllamaCLI | None = None) -> None:
        self.cli = cli or OllamaCLI()

    def modelfile(self, spec: CustomModel, spec_dir: Path) -> str:
        """The Modelfile text that would be used, without creating anything."""
        return render_modelfile(spec, adapter_path=resolve_adapter(spec, spec_dir))

    def create(self, spec: CustomModel, spec_dir: Path) -> None:
        """Write a Modelfile to a temporary directory and run ``ollama create``."""
        text = self.modelfile(spec, spec_dir)
        with tempfile.TemporaryDirectory(prefix="warnetech-modelfile-") as tmp:
            path = Path(tmp) / "Modelfile"
            path.write_text(text, encoding="utf-8")
            self.cli.create(spec.name, str(path))
