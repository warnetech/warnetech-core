"""Host resource probing and model-fit checks.

Only the standard library is used. Each probe takes its inputs as parameters
(file contents, a command runner) so the parsing logic can be tested without
the real hardware.

A model "fits" when its weights plus a fixed overhead for the KV cache and
runtime fit in the memory the model would run from: VRAM when a GPU is found,
otherwise system RAM. The overhead factor is an estimate, not a measurement.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass

log = logging.getLogger("warnetech.runtime")

# Weights plus runtime overhead. Rough, conservative rule of thumb for GGUF
# models with a default context. Measure on real hardware before relying on it.
OVERHEAD_FACTOR = 1.2
# Memory kept free for the OS and other processes.
RESERVE_GB = 2.0

Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class Resources:
    """What this host can give a model, in GiB."""

    cpu_count: int
    ram_total_gb: float | None
    ram_available_gb: float | None
    gpu_vram_total_gb: float | None = None
    gpu_vram_free_gb: float | None = None

    @property
    def budget_gb(self) -> float | None:
        """Memory a model can use: VRAM if a GPU is present, else available RAM."""
        free = self.gpu_vram_free_gb if self.gpu_vram_free_gb is not None else self.ram_available_gb
        if free is None:
            return None
        return max(0.0, free - RESERVE_GB)

    def fits(self, model_size_gb: float) -> bool:
        """Whether a model of this on-disk size is likely to run here."""
        budget = self.budget_gb
        if budget is None:
            return False  # cannot tell, so do not claim it fits
        return model_size_gb * OVERHEAD_FACTOR <= budget


_MEMINFO_RE = re.compile(r"^(\w+):\s+(\d+)\s+kB", re.MULTILINE)


def parse_meminfo(text: str) -> tuple[float | None, float | None]:
    """Return ``(total_gb, available_gb)`` from Linux ``/proc/meminfo`` text."""
    values = {key: int(kb) for key, kb in _MEMINFO_RE.findall(text)}
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    gib = 1024 * 1024
    return (
        round(total / gib, 2) if total is not None else None,
        round(available / gib, 2) if available is not None else None,
    )


def parse_nvidia_smi(text: str) -> tuple[float | None, float | None]:
    """Return ``(total_gb, free_gb)`` for the first GPU in
    ``nvidia-smi --query-gpu=memory.total,memory.free --format=csv,noheader,nounits``."""
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            total_mib, free_mib = int(parts[0]), int(parts[1])
            mib_per_gib = 1024
            return round(total_mib / mib_per_gib, 2), round(free_mib / mib_per_gib, 2)
    return None, None


def _default_runner(argv: list[str]) -> str:
    """Run a fixed argv (never a shell) and return stdout. Raises on failure."""
    completed = subprocess.run(  # noqa: S603 - argv is fixed by us, no shell
        argv, capture_output=True, text=True, timeout=10, check=True
    )
    return completed.stdout


def probe_resources(
    *,
    meminfo_text: str | None = None,
    run: Runner | None = None,
    cpu_count: int | None = None,
) -> Resources:
    """Inspect the host. Every input can be injected for tests.

    ``meminfo_text`` defaults to reading ``/proc/meminfo`` (Linux only). When it
    is absent the RAM figures are ``None``, meaning unknown, not zero.
    """
    if meminfo_text is None:
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                meminfo_text = fh.read()
        except OSError:
            meminfo_text = ""
    ram_total, ram_available = parse_meminfo(meminfo_text)

    gpu_total = gpu_free = None
    if shutil.which("nvidia-smi") or run is not None:
        runner = run or _default_runner
        try:
            out = runner(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ]
            )
            gpu_total, gpu_free = parse_nvidia_smi(out)
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("no usable GPU probe: %s", exc)

    return Resources(
        cpu_count=cpu_count or os.cpu_count() or 1,
        ram_total_gb=ram_total,
        ram_available_gb=ram_available,
        gpu_vram_total_gb=gpu_total,
        gpu_vram_free_gb=gpu_free,
    )


def recommend_model(resources: Resources, models: Iterable[tuple[str, float]]) -> str | None:
    """Pick the largest model that fits, from ``(name, size_gb)`` pairs.

    Larger usually means more capable, so this prefers size among models that
    fit. Returns ``None`` when nothing fits or the host's memory is unknown.
    """
    fitting = [(size, name) for name, size in models if resources.fits(size)]
    if not fitting:
        return None
    return max(fitting)[1]
