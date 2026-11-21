"""MCP tool definitions and dispatch for warnetech_core.

Each tool wraps a warnetech_core capability: gateway completions, embeddings,
memory/RAG, usage accounting, model listing, and the AGI orchestrator.

All tools are async and build the gateway/memory lazily on first call so the
server starts instantly and pays the cost only when a tool is actually invoked.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Lazy singleton — built once on first tool invocation
# ---------------------------------------------------------------------------

_gateway = None
_memory = None


def _ensure_built() -> tuple[Any, Any]:
    global _gateway, _memory
    if _gateway is None:
        from warnetech_core.config import build

        _gateway, _memory = build()
    return _gateway, _memory


# ---------------------------------------------------------------------------
# Tool schemas (MCP format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "warnetech_complete",
        "description": (
            "Send a prompt through the AI gateway and get a completion. "
            "Supports any registered model alias (e.g. 'chat.default', "
            "'chat.fast', 'chat.smart'). Includes retry, fallback, "
            "rate limiting and cost tracking."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The user message to send to the model.",
                },
                "model": {
                    "type": "string",
                    "description": "Model alias or ID. Default: chat.default",
                    "default": "chat.default",
                },
                "system": {
                    "type": "string",
                    "description": "Optional system message.",
                },
                "project": {
                    "type": "string",
                    "description": "Project name for usage tracking.",
                    "default": "mcp",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum tokens in the response.",
                    "default": 1024,
                },
                "temperature": {
                    "type": "number",
                    "description": "Sampling temperature (0.0 to 2.0).",
                    "default": 0.7,
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "warnetech_embed",
        "description": (
            "Generate vector embeddings for text via the AI gateway. "
            "Used for semantic search, memory storage, and RAG pipelines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of texts to embed.",
                },
                "model": {
                    "type": "string",
                    "description": "Embedding model alias. Default: embed.default",
                    "default": "embed.default",
                },
                "project": {
                    "type": "string",
                    "description": "Project for usage tracking.",
                    "default": "mcp",
                },
            },
            "required": ["texts"],
        },
    },
    {
        "name": "warnetech_remember",
        "description": (
            "Store a piece of knowledge in the memory service. "
            "Memories are namespaced by project and can be recalled "
            "later via semantic search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project namespace for the memory.",
                },
                "content": {
                    "type": "string",
                    "description": "The text content to remember.",
                },
                "key": {
                    "type": "string",
                    "description": "Optional unique key (for upsert). Auto-generated if omitted.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional metadata tags.",
                },
            },
            "required": ["project", "content"],
        },
    },
    {
        "name": "warnetech_recall",
        "description": (
            "Search memories by semantic similarity. Returns the top "
            "matches ranked by cosine similarity to the query."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project namespace to search.",
                },
                "query": {
                    "type": "string",
                    "description": "The search query text.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return.",
                    "default": 5,
                },
            },
            "required": ["project", "query"],
        },
    },
    {
        "name": "warnetech_context",
        "description": (
            "Retrieve grounded context from memory for a question (RAG). "
            "Returns a formatted context block with source citations "
            "suitable for inclusion in a prompt."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project namespace to search.",
                },
                "question": {
                    "type": "string",
                    "description": "The question to ground context for.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of context chunks to retrieve.",
                    "default": 5,
                },
            },
            "required": ["project", "question"],
        },
    },
    {
        "name": "warnetech_forget",
        "description": "Delete a specific memory by key from a project namespace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project namespace.",
                },
                "key": {
                    "type": "string",
                    "description": "The key of the memory to delete.",
                },
            },
            "required": ["project", "key"],
        },
    },
    {
        "name": "warnetech_models",
        "description": (
            "List all available models from the registry, including aliases, "
            "providers, context sizes and pricing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "warnetech_usage",
        "description": (
            "Get usage summary — total tokens, cost, and request count, "
            "optionally filtered by project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Filter to a specific project. Omit for all.",
                },
            },
        },
    },
    {
        "name": "warnetech_run_goal",
        "description": (
            "Run a goal through the AGI orchestrator. Decomposes the goal "
            "into a plan, reasons about each step, executes tools, and "
            "reflects on the outcome. Returns the full execution result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The goal to achieve.",
                },
                "success_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional measurable success criteria.",
                },
                "model": {
                    "type": "string",
                    "description": "Model alias for the orchestrator.",
                    "default": "chat.default",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "warnetech_interpret",
        "description": (
            "Ask the local interpreter what code it would run for a plain-language task. "
            "PROPOSE-ONLY: the code is returned for a person to review and is never "
            "executed by this tool."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What to do, in plain language."},
                "model": {
                    "type": "string",
                    "description": "Registry alias. Default 'chat.local'.",
                },
            },
            "required": ["task"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _complete(arguments: dict[str, Any]) -> dict[str, Any]:
    gateway, _mem = _ensure_built()
    resp = await gateway.complete(
        arguments.get("model", "chat.default"),
        arguments["prompt"],
        system=arguments.get("system"),
        project=arguments.get("project", "mcp"),
        max_tokens=arguments.get("max_tokens", 1024),
        temperature=arguments.get("temperature", 0.7),
    )
    return {
        "text": resp.text,
        "model_alias": resp.model_alias,
        "provider": resp.provider,
        "usage": {
            "input_tokens": resp.usage.input_tokens if resp.usage else 0,
            "output_tokens": resp.usage.output_tokens if resp.usage else 0,
        },
    }


async def _embed(arguments: dict[str, Any]) -> dict[str, Any]:
    gateway, _mem = _ensure_built()
    resp = await gateway.embed(
        arguments.get("model", "embed.default"),
        arguments["texts"],
        project=arguments.get("project", "mcp"),
    )
    return {
        "model_alias": resp.model_alias,
        "provider": resp.provider,
        "dimensions": len(resp.vectors[0]) if resp.vectors else 0,
        "count": len(resp.vectors),
        # Return truncated vectors for inspection; full vectors are large
        "vectors_preview": [v[:8] for v in resp.vectors] if resp.vectors else [],
    }


async def _remember(arguments: dict[str, Any]) -> dict[str, Any]:
    _gw, memory = _ensure_built()
    if memory is None:
        return {"error": "Memory service not configured"}
    record = await memory.remember(
        arguments["project"],
        arguments["content"],
        key=arguments.get("key"),
        metadata=arguments.get("metadata"),
    )
    return {
        "id": record.id,
        "key": record.key,
        "project": arguments["project"],
        "stored": True,
    }


async def _recall(arguments: dict[str, Any]) -> dict[str, Any]:
    _gw, memory = _ensure_built()
    if memory is None:
        return {"error": "Memory service not configured"}
    hits = await memory.recall(
        arguments["project"],
        arguments["query"],
        limit=arguments.get("top_k", 5),
    )
    return {
        "project": arguments["project"],
        "query": arguments["query"],
        "hits": [
            {
                "key": h.record.key,
                "content": h.record.text[:500],
                "score": round(h.score, 4),
                "metadata": h.record.metadata,
            }
            for h in hits
        ],
    }


async def _context(arguments: dict[str, Any]) -> dict[str, Any]:
    _gw, memory = _ensure_built()
    if memory is None:
        return {"error": "Memory service not configured"}
    ctx = await memory.get_context(
        arguments["project"],
        arguments["question"],
        limit=arguments.get("top_k", 5),
    )
    return {
        "project": arguments["project"],
        "question": arguments["question"],
        "text": ctx.text,
        "source_count": len(ctx.hits),
        "sources": [{"key": h.record.key, "score": round(h.score, 4)} for h in ctx.hits],
    }


async def _forget(arguments: dict[str, Any]) -> dict[str, Any]:
    _gw, memory = _ensure_built()
    if memory is None:
        return {"error": "Memory service not configured"}
    await memory.forget(arguments["project"], key=arguments["key"])
    return {"deleted": True, "project": arguments["project"], "key": arguments["key"]}


async def _models(arguments: dict[str, Any]) -> dict[str, Any]:
    gateway, _mem = _ensure_built()
    models = []
    for alias, spec in gateway.registry._specs.items():
        models.append(
            {
                "alias": alias,
                "provider": spec.provider,
                "provider_model": spec.provider_model,
                "context_window": spec.context_window,
                "input_cost_per_mtok": spec.input_cost_per_mtok,
                "output_cost_per_mtok": spec.output_cost_per_mtok,
            }
        )
    return {"models": models, "count": len(models)}


async def _usage(arguments: dict[str, Any]) -> dict[str, Any]:
    gateway, _mem = _ensure_built()
    # summary() is synchronous and returns list[UsageSummary]
    summaries = gateway.usage.summary(project=arguments.get("project"))
    total_calls = sum(s.calls for s in summaries)
    total_tokens = sum(s.total_tokens for s in summaries)
    total_cost = sum(s.cost_usd for s in summaries)
    return {
        "total_requests": total_calls,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 6),
        "groups": [
            {
                "key": s.key,
                "calls": s.calls,
                "tokens": s.total_tokens,
                "cost": round(s.cost_usd, 6),
            }
            for s in summaries
        ],
    }


async def _run_goal(arguments: dict[str, Any]) -> dict[str, Any]:
    gateway, memory = _ensure_built()

    from warnetech_core.agi.orchestrator import AGIOrchestrator
    from warnetech_core.agi.planning import Goal

    orchestrator = AGIOrchestrator(
        gateway,
        memory,
        model=arguments.get("model", "chat.default"),
    )
    goal = Goal(
        description=arguments["goal"],
        success_criteria=arguments.get("success_criteria", []),
    )
    result = await orchestrator.run(goal)
    return {
        "success": result.success,
        "summary": result.summary,
        "plan_progress": result.plan.progress() if result.plan else 0.0,
        "steps_completed": (
            sum(1 for t in result.plan.tasks if t.status.value == "completed") if result.plan else 0
        ),
        "steps_total": len(result.plan.tasks) if result.plan else 0,
    }


async def _interpret(arguments: dict[str, Any]) -> dict[str, Any]:
    """Propose code for a task. Never executes: an MCP client has no way for a
    person to approve each action, so this tool is dry-run by construction."""
    import os

    from warnetech_core.interpreter import Executor, Interpreter, Policy, Workspace

    gateway, _memory = _ensure_built()
    workspace = Workspace(os.getenv("WARNETECH_INTERPRETER_WORKSPACE") or "./workspace")
    interpreter = Interpreter(
        gateway,
        Executor(workspace),
        model=arguments.get("model", "chat.local"),
        policy=Policy(mode="dry-run"),
    )
    report = await interpreter.run(arguments["task"])
    proposal = report.steps[0].action if report.steps else None
    return {
        "executed": False,
        "stopped": report.stopped,
        "language": proposal.language if proposal else None,
        "code": proposal.code if proposal else None,
        "reply": report.final,
    }


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Any] = {
    "warnetech_complete": _complete,
    "warnetech_embed": _embed,
    "warnetech_remember": _remember,
    "warnetech_recall": _recall,
    "warnetech_context": _context,
    "warnetech_forget": _forget,
    "warnetech_models": _models,
    "warnetech_usage": _usage,
    "warnetech_run_goal": _run_goal,
    "warnetech_interpret": _interpret,
}


async def dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a tool by name. Raises KeyError for unknown tools."""
    handler = _HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"Unknown tool: {name}")
    return await handler(arguments)
