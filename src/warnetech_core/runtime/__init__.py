"""Local runtime helpers: host resources and the Ollama command-line tool.

Nothing here calls a model. Model calls go through the gateway. This package
answers the questions that come before a call: how much memory is free, which
local models are installed, and whether a model is likely to fit.
"""

from __future__ import annotations

from .daemon import DaemonManager
from .logger import get_daemon_logger, setup_daemon_logger
from .ollama_cli import OllamaCLI, OllamaModel, parse_ollama_list
from .process_supervisor import ProcessConfig, ProcessStatus, ProcessSupervisor
from .resources import Resources, probe_resources, recommend_model

__all__ = [
    "DaemonManager",
    "ProcessSupervisor",
    "ProcessConfig",
    "ProcessStatus",
    "setup_daemon_logger",
    "get_daemon_logger",
    "OllamaCLI",
    "OllamaModel",
    "Resources",
    "parse_ollama_list",
    "probe_resources",
    "recommend_model",
]
