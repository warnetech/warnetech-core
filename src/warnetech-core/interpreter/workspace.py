"""A directory the interpreter may read and write, and nothing outside it.

Every path is resolved (symlinks followed) and checked to sit under the root
before it is touched. Stripping ``..`` is not enough: absolute paths and symlinks
get past naive filters, so the resolved location is what is tested.
"""

from __future__ import annotations

from pathlib import Path

MAX_LIST_ENTRIES = 500


class WorkspaceError(Exception):
    """A path left the workspace, or a file was too large."""


class Workspace:
    def __init__(self, root: str | Path, *, max_file_bytes: int = 1_000_000) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_file_bytes = max_file_bytes

    def resolve(self, relative: str) -> Path:
        """Return the absolute path for ``relative``, or raise if it escapes."""
        if not relative or "\x00" in relative:
            raise WorkspaceError(f"invalid path: {relative!r}")
        # An absolute `relative` replaces the root in pathlib; the check below
        # then rejects it because it no longer sits under the root.
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise WorkspaceError(f"path escapes the workspace: {relative!r}")
        return candidate

    def read_text(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise WorkspaceError(f"not a file: {relative!r}")
        if path.stat().st_size > self.max_file_bytes:
            raise WorkspaceError(f"file is larger than {self.max_file_bytes} bytes: {relative!r}")
        return path.read_text(encoding="utf-8", errors="replace")

    def write_text(self, relative: str, text: str) -> int:
        data = text.encode("utf-8")
        if len(data) > self.max_file_bytes:
            raise WorkspaceError(f"content is larger than {self.max_file_bytes} bytes")
        path = self.resolve(relative)
        if path == self.root:
            raise WorkspaceError("cannot write to the workspace root")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return len(data)

    def list_dir(self, relative: str = ".") -> list[str]:
        path = self.resolve(relative)
        if not path.is_dir():
            raise WorkspaceError(f"not a directory: {relative!r}")
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        return names[:MAX_LIST_ENTRIES]
