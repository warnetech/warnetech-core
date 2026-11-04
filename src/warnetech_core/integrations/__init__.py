"""Adapters that plug warnetech-core into existing Warnetech components."""

from .ai_controller import make_embed_fn

__all__ = ["make_embed_fn"]
