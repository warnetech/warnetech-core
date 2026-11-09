"""Ordered state hooks that run on every step over one shared state dict.

Each :meth:`TemporalEngine.step` merges the input into the state, then runs the
registered hooks in order. A hook may return the new state, or return ``None``
after changing the dict in place. Hooks may be sync or async.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("warnetech.agi.temporal_engine")

Hook = Callable[[dict[str, Any]], Any]


class TemporalEngine:
    """Carry state across steps and pass it through each registered hook.

    Example::

        engine = TemporalEngine()
        engine.register_hook(lambda s: {**s, "count": s.get("count", 0) + 1})
        state = await engine.step({"task": "start"})
    """

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})
        self.registry: list[Hook] = []

    def register_hook(self, func: Hook) -> None:
        self.registry.append(func)

    async def step(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        """Merge *input_payload* into the state and run every hook once, in order."""
        log.debug("temporal step with payload keys %s", list(input_payload))
        self.state.update(input_payload)

        for hook in self.registry:
            result = hook(self.state)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                continue  # the hook changed the dict in place
            if not isinstance(result, dict):
                name = getattr(hook, "__name__", repr(hook))
                raise TypeError(
                    f"hook {name!r} must return a dict or None, not {type(result).__name__}"
                )
            self.state = result

        return self.state
