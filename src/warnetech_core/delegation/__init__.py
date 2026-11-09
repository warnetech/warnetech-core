"""Sub-agents the chat model (or you) can hand a job to: ``/delegate <agent> <task>``."""

from .runner import AgentResult, DelegationRunner
from .spec import AgentSpec, load_agents

__all__ = ["AgentResult", "AgentSpec", "DelegationRunner", "load_agents"]
