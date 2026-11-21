"""warnetech-mcp — MCP server exposing warnetech_core gateway, memory and AGI.

Run as::

    python -m warnetech_mcp

This starts a JSON-RPC 2.0 over stdio server implementing the Model Context
Protocol (MCP).  It exposes gateway completions, embeddings, memory/RAG
retrieval, usage accounting, and the AGI orchestrator as MCP tools.

Zero extra dependencies — uses only the stdlib and warnetech_core.
"""

__version__ = "0.1.0"
