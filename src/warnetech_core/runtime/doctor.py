"""`warnetech doctor`: report whether this environment can run warnetech_core.

Each check is a pure function of what it is given, so the logic is testable without
the real machine. The report names things (provider names, proxy host, file paths)
but never prints a credential: API keys are only ever tested for presence, and proxy
URLs are reduced to scheme, host and port.

The doctor does not contact hosted providers. With ``network=True`` it asks only the
local Ollama hosts that are configured, which is how a multi-machine setup is confirmed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import platform
import shutil
import struct
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..errors import ConfigError

MIN_PYTHON = (3, 10)
LOW_DISK_BYTES = 1024**3  # 1 GiB

_PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")
_CA_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
_REQUIRED = {"httpx": "httpx", "pydantic": "pydantic", "yaml": "pyyaml"}
_OPTIONAL = {
    "fastapi": "service extra",
    "uvicorn": "service extra",
    "psycopg": "pgvector extra",
    "transformers": "hf extra (local weights)",
    "torch": "hf extra (local weights)",
}


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # ok | warn | fail | info
    detail: str


def redact_proxy(value: str) -> str:
    """Reduce a proxy setting to ``scheme://host:port``; drop any user and password."""
    if "://" not in value:
        return value if "@" not in value else value.rsplit("@", 1)[1]
    parts = urlsplit(value)
    host = parts.hostname or "?"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


def detect_container(
    env: Mapping[str, str], exists: Callable[[str], bool], read_text: Callable[[str], str]
) -> str | None:
    """Name the container runtime if there is evidence of one, else ``None``."""
    if env.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    if exists("/.dockerenv"):
        return "docker"
    try:
        cgroup = read_text("/proc/1/cgroup")
    except OSError:
        return None
    for marker in ("kubepods", "docker", "containerd", "podman", "lxc"):
        if marker in cgroup:
            return marker
    return None


def detect_android(env: Mapping[str, str], sys_platform: str) -> str | None:
    """Name the Android environment if there is evidence of one, else ``None``.

    Termux sets ``TERMUX_VERSION``; every Android process has ``ANDROID_ROOT``; CPython 3.13+
    reports ``sys.platform == "android"`` when built for the platform.
    """
    if env.get("TERMUX_VERSION"):
        return "termux"
    if sys_platform == "android" or (env.get("ANDROID_ROOT") and env.get("ANDROID_DATA")):
        return "android"
    return None


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def run_checks(
    *,
    env: Mapping[str, str] | None = None,
    python_version: tuple[int, int] | None = None,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
    exists: Callable[[str], bool] = os.path.exists,
    read_text: Callable[[str], str] = _read,
    access: Callable[[str, int], bool] = os.access,
    disk_free: Callable[[str], int] = lambda p: shutil.disk_usage(p).free,
    network: bool = False,
    machine: str | None = None,
    pointer_bits: int | None = None,
    sys_platform: str | None = None,
    memory_limit_enforced: bool | None = None,
) -> list[Check]:
    env = os.environ if env is None else env
    if memory_limit_enforced is None:
        from ..interpreter.executor import MEMORY_LIMIT_ENFORCED

        memory_limit_enforced = MEMORY_LIMIT_ENFORCED
    py = python_version or (sys.version_info.major, sys.version_info.minor)
    checks: list[Check] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append(Check(name, status, detail))

    # ---- platform ----
    if py < MIN_PYTHON:
        add("python", "fail", f"{py[0]}.{py[1]} is too old; 3.10 or newer is required")
    else:
        add("python", "ok", f"{py[0]}.{py[1]} on {platform.system()} {platform.machine()}")
    arch = (machine if machine is not None else platform.machine()).lower()
    bits = pointer_bits if pointer_bits is not None else struct.calcsize("P") * 8
    android = detect_android(env, sys_platform if sys_platform is not None else sys.platform)
    if android:
        add("android", "info", f"running on Android ({android}), {arch}, {bits}-bit Python")
    if bits == 32:
        add(
            "python bits",
            "warn",
            "32-bit Python: pydantic-core has no wheels and large models will not fit in "
            "memory; use a 64-bit (arm64-v8a / x86_64) Python"
            + (" - Termux on a 64-bit device" if android else ""),
        )
    elif android and arch not in {"aarch64", "arm64", "x86_64"}:
        add("android", "warn", f"unexpected architecture {arch!r} for 64-bit Android")
    container = detect_container(env, exists, read_text)
    add("container", "info", f"running in {container}" if container else "not in a container")
    if not memory_limit_enforced:
        add(
            "interpreter memory limit",
            "warn",
            "not enforced on this platform (Linux only), so the interpreter refuses to run code "
            "unless WARNETECH_ALLOW_UNENFORCED_MEMORY=1",
        )

    # ---- dependencies ----
    missing = [pkg for mod, pkg in _REQUIRED.items() if find_spec(mod) is None]
    if missing:
        add("dependencies", "fail", f"missing required packages: {', '.join(missing)}")
    else:
        add("dependencies", "ok", "required packages present")
    present = sorted(f"{m} ({why})" for m, why in _OPTIONAL.items() if find_spec(m) is not None)
    add("extras", "info", ", ".join(present) if present else "no optional extras installed")
    if find_spec("transformers") is not None and py < (3, 11):
        add(
            "hf",
            "warn",
            "transformers is installed but the local-weights backend needs Python 3.11+",
        )

    # ---- mode ----
    offline = env.get("WARNETECH_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}
    local_only = env.get("WARNETECH_LOCAL_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}
    add(
        "mode",
        "info",
        f"offline={'on' if offline else 'off'}, local-only={'on' if local_only else 'off'}",
    )

    # ---- providers (the real registration path; keys are never shown) ----
    try:
        from ..gateway import AIGateway

        # The provider list is reported below, so the gateway's own "no keys" warning would
        # only add noise, and could end up ahead of the JSON on a stream a script reads.
        gateway_log = logging.getLogger("warnetech.gateway")
        previous_level = gateway_log.level
        gateway_log.setLevel(logging.ERROR)
        try:
            gateway = AIGateway.from_env(local_only=local_only)
        finally:
            gateway_log.setLevel(previous_level)
        names = sorted(gateway.providers)
        add("providers", "ok", ", ".join(names))
        hosted = [n for n in ("anthropic", "openai", "nvidia") if env.get(f"{n.upper()}_API_KEY")]
        if local_only and hosted:
            add("local-only", "info", f"keys for {', '.join(hosted)} are set but ignored")
    except ConfigError as exc:
        add("providers", "fail", str(exc))

    # ---- network configuration ----
    for var in _PROXY_VARS:
        if env.get(var):
            add(f"proxy {var}", "info", redact_proxy(env[var]))
    for var in _CA_VARS:
        if env.get(var):
            ok = exists(env[var])
            add(
                f"ca {var}",
                "ok" if ok else "warn",
                env[var] if ok else f"{env[var]} does not exist",
            )

    # ---- paths ----
    for var, default in (
        ("WARNETECH_MEMORY_PATH", "./data/warnetech_memory.db"),
        ("WARNETECH_USAGE_PATH", "./data/warnetech_usage.db"),
    ):
        target = Path(env.get(var, default)).resolve()
        parent = target.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        label = var.removeprefix("WARNETECH_").removesuffix("_PATH").lower()
        if not access(str(parent), os.W_OK):
            add(f"data {label}", "warn", f"{parent} is not writable")
        elif disk_free(str(parent)) < LOW_DISK_BYTES:
            add(f"data {label}", "warn", f"under 1 GiB free at {parent}")
        else:
            add(f"data {label}", "ok", f"writable, {disk_free(str(parent)) // 1024**3} GiB free")
    for var in ("WARNETECH_LOCAL_MODELS", "WARNETECH_ROUTING_FILE", "WARNETECH_CUSTOM_MODELS_DIR"):
        if env.get(var) and not exists(env[var]):
            add(var.lower(), "warn", f"{env[var]} does not exist")

    # ---- resources ----
    from .resources import probe_resources

    res = probe_resources()
    ram = "unknown" if res.ram_available_gb is None else f"{res.ram_available_gb:.1f} GB free"
    gpu = (
        "no NVIDIA GPU found"
        if res.gpu_vram_total_gb is None
        else f"GPU {res.gpu_vram_free_gb:.1f} GB free"
    )
    add("resources", "info", f"{res.cpu_count} cores, RAM {ram}, {gpu}")

    # ---- local hosts (only asked when requested) ----
    if network:
        from ..providers.ollama import ollama_base_url, parse_nodes
        from .nodes import check_nodes

        nodes: dict[str, str] = {}
        try:
            if env.get("OLLAMA_HOST"):
                nodes["ollama"] = ollama_base_url(env["OLLAMA_HOST"])
            nodes.update(parse_nodes(env.get("OLLAMA_NODES")))
        except ConfigError as exc:
            add("ollama hosts", "fail", str(exc))
        if nodes:
            for status in asyncio.run(check_nodes(nodes)):
                detail = f"{len(status.models)} models" if status.reachable else status.error
                add(f"host {status.name}", "ok" if status.reachable else "fail", detail)
        elif not any(c.name == "ollama hosts" for c in checks):
            add("ollama hosts", "info", "none configured (set OLLAMA_HOST or OLLAMA_NODES)")
    return checks


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == "fail" for c in checks) else 0


def format_text(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    return "\n".join(f"{c.status:<5} {c.name:<{width}}  {c.detail}" for c in checks)


def to_dicts(checks: list[Check]) -> list[dict[str, str]]:
    return [asdict(c) for c in checks]
