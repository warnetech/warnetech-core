"""Standalone engine bootstrap for warnetech_core.

Provides :func:`main` as the entry point invoked by ``warnetech.sh``.
"""

from .engine import main

__all__ = ["main"]
