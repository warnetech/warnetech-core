"""Adapters that plug warnetech_core into existing Warnetech components."""

from .ai_controller import make_embed_fn

__all__ = ["make_embed_fn"]
