#!/usr/bin/env python3
"""warnetech_core engine bootstrap.

Standalone entry point for the AGI orchestrator, invoked via ``warnetech.sh``.
Loads environment, builds the gateway and memory service, then dispatches
the requested task through the orchestrator or a simpler gateway call.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

log = logging.getLogger("warnetech.engine")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="warnetech-engine",
        description="WarNetTech Core Intelligence Engine",
    )
    sub = p.add_subparsers(dest="command")

    ask = sub.add_parser("ask", help="Send a prompt through the gateway")
    ask.add_argument("prompt", nargs="+", help="The prompt text")
    ask.add_argument("--model", default="chat.default")
    ask.add_argument("--project", default="engine")

    run = sub.add_parser("run", help="Run a goal through the AGI orchestrator")
    run.add_argument("goal", nargs="+", help="Goal description")
    run.add_argument("--model", default="chat.default")

    sub.add_parser("status", help="Show engine configuration status")
    return p


async def _ask(args: argparse.Namespace) -> int:
    from warnetech_core.config import build

    gateway, _memory = build()
    prompt = " ".join(args.prompt)
    response = await gateway.complete(
        args.model,
        prompt,
        project=args.project,
    )
    print(response.text)
    return 0


async def _run_goal(args: argparse.Namespace) -> int:
    from warnetech_core.agi.orchestrator import AGIOrchestrator
    from warnetech_core.agi.planning import Goal
    from warnetech_core.config import build

    gateway, memory = build()
    orchestrator = AGIOrchestrator(gateway, memory, model=args.model)
    goal = Goal(description=" ".join(args.goal))
    result = await orchestrator.run(goal)
    print(result.summary)
    return 0 if result.success else 1


def _status() -> int:
    import os

    offline = os.environ.get("WARNETECH_OFFLINE", "0") == "1"
    log.info(
        "engine_status",
        extra={
            "offline": offline,
            "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
        },
    )
    print(f"offline={offline}")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "ask":
        return asyncio.run(_ask(args))
    if args.command == "run":
        return asyncio.run(_run_goal(args))
    if args.command == "status":
        return _status()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
