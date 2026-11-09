"""Check which Ollama machines are reachable and which models each one has.

Used by `warnetech ollama nodes`. It asks each host for its model list
(``GET /api/tags``), which is how a multi-machine setup is confirmed to be wired
up before work is sent to it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True)
class NodeStatus:
    name: str
    url: str
    reachable: bool
    models: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""


def _host(base_url: str) -> str:
    """The Ollama root URL, given the OpenAI-style ``.../v1`` URL used elsewhere."""
    root = base_url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


async def check_node(
    name: str, base_url: str, *, client: httpx.AsyncClient | None = None, timeout: float = 5.0
) -> NodeStatus:
    """Ask one host for its models. Never raises: failure is part of the result."""
    url = f"{_host(base_url)}/api/tags"
    owns = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        response = await http.get(url)
        if response.status_code != 200:
            return NodeStatus(name, base_url, False, error=f"HTTP {response.status_code}")
        try:
            names = tuple(sorted(m["name"] for m in response.json().get("models", [])))
        except (ValueError, KeyError, TypeError, AttributeError):
            return NodeStatus(name, base_url, False, error="unexpected response body")
        return NodeStatus(name, base_url, True, models=names)
    except httpx.HTTPError as exc:
        return NodeStatus(name, base_url, False, error=type(exc).__name__)
    finally:
        if owns:
            await http.aclose()


async def check_nodes(
    nodes: Mapping[str, str], *, client: httpx.AsyncClient | None = None, timeout: float = 5.0
) -> list[NodeStatus]:
    """Check every node at once, in the order given."""
    return list(
        await asyncio.gather(
            *(check_node(n, url, client=client, timeout=timeout) for n, url in nodes.items())
        )
    )
