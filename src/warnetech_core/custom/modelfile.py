"""Render a validated spec as an Ollama Modelfile.

The spec has already been checked, so this only formats. Each directive goes on
its own line, and values that could break out of a line were rejected upstream.
"""

from __future__ import annotations

from pathlib import Path

from .spec import CustomModel


def render_modelfile(spec: CustomModel, *, adapter_path: Path | None = None) -> str:
    """Return the Modelfile text for ``spec``.

    ``adapter_path`` is the resolved absolute path of the adapter, supplied by the
    builder after it has confirmed the file exists inside the spec's directory.
    """
    lines: list[str] = []
    if spec.description:
        lines.append(f"# {spec.description}")
    lines.append(f"FROM {spec.base}")
    if adapter_path is not None:
        lines.append(f"ADAPTER {adapter_path}")
    for key in sorted(spec.parameters):
        lines.append(f"PARAMETER {key} {spec.parameters[key]}")
    for item in spec.stop:
        lines.append(f'PARAMETER stop "{item}"')
    if spec.system:
        lines.append(f'SYSTEM """{spec.system}"""')
    return "\n".join(lines) + "\n"
