"""Context engineering: deciding what the model gets to see.

Retrieval quality dominates almost every other knob in a code-facing LLM
feature. A weaker model with the right three files beats a stronger model with
the wrong thirty, and it costs a tenth as much.

This module packs a repository into a token budget:

* :func:`walk_repo`     -- collect candidate files, respecting ignores.
* :func:`repo_map`      -- a cheap structural outline (signatures, not bodies).
* :func:`rank`          -- score files against a query with explainable signals.
* :func:`pack`          -- fill a budget, best-first, with elision that says so.

The estimator is chars/4 by default. When accuracy matters, pass a counter
backed by ``client.messages.count_tokens`` -- never ``tiktoken``, which models a
different tokenizer entirely.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_IGNORES = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", ".next", ".nuxt", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", "vendor", "coverage", ".terraform", ".gradle", ".idea",
}

DEFAULT_EXTENSIONS = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java",
    ".kt", ".rb", ".php", ".cs", ".swift", ".scala", ".c", ".h", ".cc", ".cpp",
    ".hpp", ".sql", ".sh", ".md", ".rst", ".toml", ".yaml", ".yml", ".json",
}

# Files that disproportionately explain a repo to a reader who has never seen it.
ANCHOR_NAMES = {
    "readme.md", "architecture.md", "claude.md", "agents.md", "contributing.md",
    "pyproject.toml", "package.json", "cargo.toml", "go.mod", "makefile",
}

TokenCounter = Callable[[str], int]
Embedder = Callable[[str], list[float]]
"""Text to a vector. Any embedder will do; llmforge never assumes which."""

EMBEDDING_DIMS = 128


def hash_embed(text: str, dims: int = EMBEDDING_DIMS) -> list[float]:
    """A deterministic hash-projection embedding. No dependency, no network.

    **This is not semantic.** Two paraphrases of the same idea get unrelated
    vectors. It detects near-duplicates and nothing else, so as a *ranking*
    signal it contributes approximately nothing -- which is why ``embed`` is
    off by default in :func:`rank`.

    Its job is to keep the embedding seam exercised: a code path that only
    runs when someone wires up a real embedder is a code path that is broken
    the first time someone does.

    Swap in a real embedder -- a sentence transformer, a hosted API -- via the
    ``embed`` parameter on :func:`rank`. Nothing here assumes which.

    Adapted from the embeddings module in tewartech-node/claude-command-cli,
    which made the same call: ship a deterministic default so the code path is
    live, and let callers pay for semantics when they need them.
    """
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).digest()
    raw = (digest * ((dims // len(digest)) + 1))[:dims]
    # Centred on zero, not scaled from zero. The obvious `b / 255` puts every
    # vector in the positive orthant, where unrelated strings score ~0.77
    # cosine similarity -- a floor high enough that the signal is swamped by
    # it. Centring restores ~0 for unrelated text, which is what a ranking
    # weight needs.
    vector = [(b - 127.5) / 127.5 for b in raw]
    magnitude = math.sqrt(sum(v * v for v in vector))
    return [v / magnitude for v in vector] if magnitude else vector


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, clamped to [0, 1]. Zero for mismatched or empty input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    magnitude = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return max(0.0, min(1.0, dot / magnitude)) if magnitude else 0.0


def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 characters per token.

    Good enough for budgeting, wrong enough that you should not bill on it.
    """
    return max(1, len(text) // 4)


@dataclass(slots=True)
class Chunk:
    """A candidate piece of context with a provenance trail."""

    path: str
    text: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    tokens: int = 0
    kind: str = "file"
    similarity: float = 0.0

    def render(self) -> str:
        return f"<file path=\"{self.path}\">\n{self.text}\n</file>"


def walk_repo(
    root: str | Path,
    *,
    extensions: Iterable[str] | None = None,
    ignores: Iterable[str] | None = None,
    max_bytes: int = 400_000,
) -> list[Path]:
    """Collect candidate source files under ``root``.

    Skips ignored directories, unknown extensions, and files large enough to be
    generated (lockfiles, bundles, fixtures) -- those blow a budget without
    teaching the model anything.
    """
    root = Path(root)
    exts = set(extensions or DEFAULT_EXTENSIONS)
    skip = set(ignores or DEFAULT_IGNORES)
    found: list[Path] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if set(path.parts) & skip:
            continue
        if path.suffix.lower() not in exts:
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
        except OSError:  # pragma: no cover - races and permissions
            continue
        found.append(path)
    return found


# --------------------------------------------------------------------------- #
# Structural map
# --------------------------------------------------------------------------- #

_SIGNATURE_PATTERNS = (
    re.compile(r"^\s*(?:async\s+)?def\s+\w+\s*\(", re.M),
    re.compile(r"^\s*class\s+\w+", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+\w+", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+\w+", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+\w+\s*[:=]\s*(?:async\s*)?\(", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+\w+", re.M),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?\w+\s*\(", re.M),
    re.compile(r"^\s*(?:pub\s+)?fn\s+\w+", re.M),
)


def outline(text: str, *, limit: int = 60) -> list[str]:
    """Extract declaration lines from source text.

    Regex, not a parser. That is a deliberate trade: it works on every language
    at once, degrades to "fewer lines" instead of "crash", and needs no
    per-language dependency. When you need real fidelity for one language, swap
    in that language's AST here.
    """
    hits: list[tuple[int, str]] = []
    for pattern in _SIGNATURE_PATTERNS:
        for match in pattern.finditer(text):
            end = text.find("\n", match.start())
            line = text[match.start() : end if end != -1 else len(text)]
            hits.append((match.start(), line.rstrip().rstrip("{:")))
    seen: set[str] = set()
    ordered: list[str] = []
    for _, line in sorted(hits):
        stripped = line.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            ordered.append(stripped)
        if len(ordered) >= limit:
            break
    return ordered


def repo_map(paths: Sequence[Path], root: str | Path, *, per_file: int = 12) -> str:
    """A compact structural map: paths plus their top declarations.

    Put this in the *stable prefix* of a long-running session. It is small, it
    changes rarely, and it lets the model ask for the right file by name instead
    of guessing -- which is what actually collapses retrieval cost.
    """
    root = Path(root)
    lines: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover
            continue
        rel = path.relative_to(root).as_posix()
        decls = outline(text, limit=per_file)
        lines.append(f"{rel}")
        lines.extend(f"  {d}" for d in decls)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _terms(text: str) -> set[str]:
    """Tokenize into lowercase terms, splitting camelCase and snake_case."""
    out: set[str] = set()
    for word in _WORD.findall(text):
        out.add(word.lower())
        for part in re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", word):
            if len(part) > 2:
                out.add(part.lower())
    return out


def rank(
    query: str,
    paths: Sequence[Path],
    root: str | Path,
    *,
    pinned: Iterable[str] = (),
    recent: Iterable[str] = (),
    embed: Embedder | None = None,
    embedding_weight: float = 15.0,
) -> list[Chunk]:
    """Score files against a query, with the reason for each score attached.

    Signals, in rough order of how much they matter in practice:

    * a path the caller pinned (always first -- the human knows something)
    * query terms appearing in the *path* (a strong, cheap signal)
    * query terms appearing in the *body*, saturating so one file cannot win by
      repeating a word two hundred times
    * recent edits (git status, open editors) -- proximity to the current task
    * anchor files that orient a reader who has never seen the repo
    * semantic similarity, when an ``embed`` function is supplied

    ``embed`` is off by default and that is deliberate. Lexical signals are
    free, explainable, and on a codebase they are strong -- identifiers are
    shared vocabulary, not prose. An embedder adds latency and cost per file
    and only pays for itself when the query and the code use *different* words
    for the same thing ("why is checkout slow" against a file that never says
    "slow"). Turn it on when you have measured that case, not before.

    Semantic similarity is blended with, never substituted for, the lexical
    score: an embedder that silently returns garbage would otherwise take the
    ranking down with it.

    The ``reasons`` list is the point. A retrieval step you cannot explain is a
    retrieval step you cannot debug when it starts returning the wrong files.
    """
    root = Path(root)
    wanted = _terms(query)
    pinned_set = {Path(p).as_posix() for p in pinned}
    recent_set = {Path(p).as_posix() for p in recent}
    query_vector: list[float] | None = None
    embed_error: str | None = None
    if embed is not None:
        try:
            query_vector = embed(query)
        except Exception as exc:  # noqa: BLE001 - retrieval degrades, never dies
            embed_error = f"embedding failed: {type(exc).__name__}"
    chunks: list[Chunk] = []

    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover
            continue

        score = 0.0
        reasons: list[str] = []

        if rel in pinned_set:
            score += 1000.0
            reasons.append("pinned")

        path_hits = wanted & _terms(rel)
        if path_hits:
            score += 12.0 * len(path_hits)
            reasons.append(f"path matches {sorted(path_hits)}")

        body = _terms(text)
        body_hits = wanted & body
        if body_hits:
            # Saturating: the 10th distinct match matters much less than the 1st.
            score += 20.0 * (len(body_hits) / (len(body_hits) + 4))
            reasons.append(f"body matches {len(body_hits)} terms")

        if rel in recent_set:
            score += 25.0
            reasons.append("recently edited")

        if path.name.lower() in ANCHOR_NAMES:
            score += 8.0
            reasons.append("anchor file")

        similarity = 0.0
        if embed_error:
            reasons.append(embed_error)
        elif query_vector is not None and embed is not None:
            try:
                similarity = cosine_similarity(query_vector, embed(text))
            except Exception as exc:  # noqa: BLE001 - one bad file must not sink the rest
                reasons.append(f"embedding failed: {type(exc).__name__}")
            else:
                if similarity > 0:
                    score += embedding_weight * similarity
                    reasons.append(f"semantic similarity {similarity:.2f}")

        # Mild preference for smaller files at equal relevance: three small
        # files usually explain more than one large one for the same budget.
        score -= min(5.0, len(text) / 20_000)

        chunks.append(
            Chunk(
                path=rel,
                text=text,
                score=score,
                reasons=reasons,
                tokens=estimate_tokens(text),
                similarity=similarity,
            )
        )

    chunks.sort(key=lambda c: (-c.score, c.path))
    return chunks


# --------------------------------------------------------------------------- #
# Packing
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Packed:
    """The result of filling a budget."""

    text: str
    tokens: int
    included: list[str]
    elided: list[str]
    budget: int

    @property
    def utilization(self) -> float:
        return self.tokens / self.budget if self.budget else 0.0


def pack(
    chunks: Sequence[Chunk],
    *,
    budget_tokens: int = 60_000,
    counter: TokenCounter = estimate_tokens,
    head_lines: int = 120,
    tail_lines: int = 40,
    min_chunk_tokens: int = 200,
) -> Packed:
    """Fill a token budget best-first, truncating visibly rather than silently.

    Two rules earn their keep:

    * **Never silently truncate.** A file that got cut carries an explicit
      ``... N lines elided ...`` marker, so the model knows to ask for the rest
      instead of confidently reasoning about code it never saw.
    * **Skip, do not shrink to nothing.** Below ``min_chunk_tokens`` a fragment
      is noise; the budget is better spent finishing the next file.
    """
    parts: list[str] = []
    included: list[str] = []
    elided: list[str] = []
    used = 0

    for chunk in chunks:
        remaining = budget_tokens - used
        if remaining <= min_chunk_tokens:
            elided.append(chunk.path)
            continue

        body = chunk.text
        cost = counter(body)

        if cost > remaining:
            lines = body.splitlines()
            if len(lines) > head_lines + tail_lines:
                cut = len(lines) - head_lines - tail_lines
                body = "\n".join(
                    lines[:head_lines]
                    + [f"\n... {cut} lines elided from {chunk.path} ...\n"]
                    + lines[-tail_lines:]
                )
                cost = counter(body)
            if cost > remaining:
                elided.append(chunk.path)
                continue

        parts.append(Chunk(path=chunk.path, text=body).render())
        included.append(chunk.path)
        used += cost

    if elided:
        parts.append(
            "<elided-files note=\"not included; ask for any of these by path if needed\">\n"
            + "\n".join(elided)
            + "\n</elided-files>"
        )

    return Packed(
        text="\n\n".join(parts),
        tokens=used,
        included=included,
        elided=elided,
        budget=budget_tokens,
    )


def build_context(
    query: str,
    root: str | Path,
    *,
    budget_tokens: int = 60_000,
    pinned: Iterable[str] = (),
    recent: Iterable[str] = (),
    counter: TokenCounter = estimate_tokens,
    embed: Embedder | None = None,
) -> Packed:
    """walk -> rank -> pack, in one call. The 90% path."""
    paths = walk_repo(root)
    ranked = rank(query, paths, root, pinned=pinned, recent=recent, embed=embed)
    return pack(ranked, budget_tokens=budget_tokens, counter=counter)
