"""Natural-language computer control with local models.

Describe a task; a model writes code; a person approves it; it runs in a
workspace with limits; the output goes back to the model. Works against local
backends (Ollama, LM Studio) with no internet and no tokens.

Entry points for other systems::

    from warnetech_core.interpreter import Interpreter, Executor, Workspace, Policy

    workspace = Workspace("./workspace")
    interpreter = Interpreter(gateway, Executor(workspace), approver=my_approver)
    report = await interpreter.run("count the lines in every .py file")

Safety is layered, not absolute: approval of every action, a confined workspace,
a scrubbed environment, resource limits and an audit log. See ``executor.py`` for
what is and is not enforced.
"""

from __future__ import annotations

from .agent_tools import register_interpreter_tools
from .audit import AuditLog
from .executor import ExecResult, ExecutionRefused, Executor
from .parser import CodeBlock, first_block
from .policy import Action, Approver, Policy, deny_all, prompt_approver
from .session import Interpreter, RunReport, Step
from .workspace import Workspace, WorkspaceError

__all__ = [
    "Action",
    "Approver",
    "AuditLog",
    "CodeBlock",
    "ExecResult",
    "ExecutionRefused",
    "Executor",
    "Interpreter",
    "Policy",
    "RunReport",
    "Step",
    "Workspace",
    "WorkspaceError",
    "deny_all",
    "first_block",
    "prompt_approver",
    "register_interpreter_tools",
]
