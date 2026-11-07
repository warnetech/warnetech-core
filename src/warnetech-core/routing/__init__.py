"""Send each task to the model suited to it, and chain models into pipelines.

- ``classify``  a rule-based guess at what kind of task some text is
- ``router``    picks the first available model for that kind, from a table
- ``pipeline``  runs steps in order, each on its own model, passing output along

Every model call still goes through the gateway.
"""

from __future__ import annotations

from .classify import KINDS, Classification, classify
from .pipeline import Pipeline, PipelineResult, Step, load_pipeline, render, run_pipeline
from .router import Route, RoutingError, TaskRouter, load_table

__all__ = [
    "KINDS",
    "Classification",
    "Pipeline",
    "PipelineResult",
    "Route",
    "RoutingError",
    "Step",
    "TaskRouter",
    "classify",
    "load_pipeline",
    "load_table",
    "render",
    "run_pipeline",
]
