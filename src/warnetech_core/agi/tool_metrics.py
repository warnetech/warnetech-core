"""Tool metrics, Bayesian ranking and adaptive selection (Phase 2).

Each tool's success rate is modelled as a Beta posterior over observed
successes and failures. Ranking uses the posterior mean. Adaptive selection
draws one Thompson sample per candidate and picks the highest, so tools with
little data still get explored. Stdlib only, no model calls.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, Field

from .state import StateError


class ToolStats(BaseModel):
    """Raw counters for one tool."""

    successes: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    total_latency_ms: float = Field(default=0.0, ge=0.0)

    @property
    def trials(self) -> int:
        return self.successes + self.failures

    @property
    def mean_latency_ms(self) -> float | None:
        return self.total_latency_ms / self.trials if self.trials else None


class ToolMetrics:
    """Per-tool outcome counters with Beta-prior scoring.

    ``prior_alpha`` and ``prior_beta`` are the Beta prior pseudo-counts.
    The default (1, 1) is the uniform prior: an untried tool scores 0.5.
    """

    def __init__(self, prior_alpha: float = 1.0, prior_beta: float = 1.0) -> None:
        if prior_alpha <= 0 or prior_beta <= 0:
            raise ValueError("prior pseudo-counts must be positive")
        self._alpha = prior_alpha
        self._beta = prior_beta
        self._stats: dict[str, ToolStats] = {}

    # --- recording -----------------------------------------------------

    def record(self, tool: str, *, success: bool, latency_ms: float = 0.0) -> None:
        if latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        stats = self._stats.setdefault(tool, ToolStats())
        if success:
            stats.successes += 1
        else:
            stats.failures += 1
        stats.total_latency_ms += latency_ms

    def stats(self, tool: str) -> ToolStats:
        return self._stats.get(tool, ToolStats()).model_copy()

    def tools(self) -> list[str]:
        return sorted(self._stats)

    # --- Bayesian scoring ----------------------------------------------

    def posterior_params(self, tool: str) -> tuple[float, float]:
        """Beta(alpha, beta) posterior parameters for ``tool``."""
        s = self._stats.get(tool)
        successes = s.successes if s else 0
        failures = s.failures if s else 0
        return self._alpha + successes, self._beta + failures

    def posterior_mean(self, tool: str) -> float:
        alpha, beta = self.posterior_params(tool)
        return alpha / (alpha + beta)

    def rank(self, candidates: Iterable[str]) -> list[str]:
        """Candidates sorted by posterior mean, best first. Ties sort by name."""
        names = list(dict.fromkeys(candidates))
        return sorted(names, key=lambda t: (-self.posterior_mean(t), t))

    # --- adaptive selection --------------------------------------------

    def choose(
        self,
        candidates: Iterable[str],
        *,
        rng: random.Random | None = None,
    ) -> str | None:
        """Pick one candidate by Thompson sampling. ``None`` if no candidates."""
        names = list(dict.fromkeys(candidates))
        if not names:
            return None
        sampler = rng if rng is not None else random.Random()
        best_name: str | None = None
        best_draw = -1.0
        for name in names:
            alpha, beta = self.posterior_params(name)
            draw = sampler.betavariate(alpha, beta)
            if draw > best_draw:
                best_name, best_draw = name, draw
        return best_name

    # --- persistence ---------------------------------------------------

    def to_dict(self) -> dict[str, dict[str, float]]:
        return {name: s.model_dump() for name, s in sorted(self._stats.items())}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        prior_alpha: float = 1.0,
        prior_beta: float = 1.0,
    ) -> ToolMetrics:
        metrics = cls(prior_alpha=prior_alpha, prior_beta=prior_beta)
        if not path.exists():
            return metrics
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError(f"corrupt tool metrics file: {path}") from exc
        for name, raw in data.items():
            metrics._stats[name] = ToolStats(**raw)
        return metrics
