"""Load documents into persistent memory: read, chunk, embed, store.

Re-ingesting a file is idempotent: chunks are keyed by ``<path>#<n>``, so the same file
updates in place and chunks a shorter file no longer has are removed. Files that could
hold secrets or are not text are skipped, and symlinks are never followed.
"""

from __future__ import annotations

import fnmatch
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .service import MemoryService

TEXT_SUFFIXES = frozenset({
    ".md", ".txt", ".rst", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
    ".sh", ".html", ".css", ".csv", ".ini", ".cfg", ".sql", ".java", ".go", ".rs", ".c", ".h",
})
# Never ingested, whatever the extension: these commonly hold credentials.
SECRET_PATTERNS = (".env*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*",
                   "*secret*", "*credential*", "*.kdbx", "*.token")
MAX_FILE_BYTES = 1_000_000
CHUNK_CHARS = 1200
OVERLAP_CHARS = 150


@dataclass
class IngestReport:
    files: int = 0
    chunks: int = 0
    removed: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Split on paragraph boundaries into chunks of at most ``size`` characters.

    Each chunk after the first starts with the tail of the one before, so a fact that
    straddles a boundary is whole in at least one chunk. A single paragraph longer than
    ``size`` is cut at the limit.
    """
    if size <= overlap:
        raise ValueError("size must exceed overlap")
    pieces: list[str] = []
    for para in (p.strip() for p in text.replace("\r\n", "\n").split("\n\n")):
        while len(para) > size:
            pieces.append(para[:size])
            para = para[size - overlap:]
        if para:
            pieces.append(para)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > size:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{piece}" if tail and len(tail) + 2 + len(piece) <= size else piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current.strip():
        chunks.append(current)
    return chunks


def is_secret_name(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pat) for pat in SECRET_PATTERNS)


def iter_files(root: Path, report: IngestReport) -> Iterator[Path]:
    """Yield ingestible files under ``root`` (or ``root`` itself), recording what is skipped."""
    candidates = [root] if root.is_file() or root.is_symlink() else sorted(root.rglob("*"))
    for path in candidates:
        rel = path.name if root.is_file() else str(path.relative_to(root))
        if path.is_dir() and not path.is_symlink():
            continue
        parts = path.relative_to(root).parts if root.is_dir() else (path.name,)
        if any(part.startswith(".") for part in parts):
            report.skipped.append((rel, "hidden"))
        elif path.is_symlink():
            report.skipped.append((rel, "symlink"))
        elif is_secret_name(path.name):
            report.skipped.append((rel, "may hold secrets"))
        elif path.suffix.lower() not in TEXT_SUFFIXES:
            report.skipped.append((rel, "not a text type"))
        elif path.stat().st_size > MAX_FILE_BYTES:
            report.skipped.append((rel, "too large"))
        else:
            yield path


async def ingest_path(
    memory: MemoryService, project: str, target: str | Path, *, scope: str = "docs"
) -> IngestReport:
    root = Path(target)
    report = IngestReport()
    if not root.exists():
        raise FileNotFoundError(f"{root} does not exist")
    base = root if root.is_dir() else root.parent
    for path in iter_files(root, report):
        rel = str(path.relative_to(base))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            report.skipped.append((rel, f"unreadable: {exc}"))
            continue
        if b"\x00" in raw:
            report.skipped.append((rel, "binary"))
            continue
        chunks = chunk_text(raw.decode("utf-8", errors="replace"))
        digest = hashlib.sha256(raw).hexdigest()[:16]
        items = [
            {
                "text": chunk,
                "key": f"{rel}#{i}",
                "metadata": {"source": rel, "chunk": i, "of": len(chunks), "sha": digest},
            }
            for i, chunk in enumerate(chunks)
        ]
        if items:
            await memory.remember_many(project, items, scope=scope)
        keep = {item["key"] for item in items}
        for old in await memory.list(project, scope=scope, limit=10_000,
                                     metadata_filter={"source": rel}):
            if old.key not in keep:
                report.removed += await memory.forget(project, key=old.key, scope=scope)
        report.files += 1
        report.chunks += len(items)
    return report
