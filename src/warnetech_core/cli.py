"""``warnetech`` command line — inspect the registry and the bill.

Mostly for answering "what is this costing us" and "which model does
``chat.default`` actually hit right now" without opening a Python REPL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from .config import Settings, build


def _table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "(no rows)"
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    rule = "  ".join("-" * widths[c] for c in columns)
    body = "\n".join("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns) for r in rows)
    return f"{header}\n{rule}\n{body}"


def cmd_models(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    registry = settings.build_registry()
    if args.json:
        print(
            json.dumps(
                {
                    "aliases": registry.aliases(),
                    "models": [
                        {
                            "alias": s.alias,
                            "provider": s.provider,
                            "provider_model": s.provider_model,
                        }
                        for s in registry.specs()
                    ],
                },
                indent=2,
            )
        )
        return 0
    print("Task aliases")
    print(
        _table(
            [
                {"alias": k, "resolves to": registry.resolve(k).alias}
                for k in sorted(registry.aliases())
            ],
            ["alias", "resolves to"],
        )
    )
    print("\nModels")
    print(
        _table(
            [
                {
                    "alias": s.alias,
                    "provider": s.provider,
                    "model": s.provider_model,
                    "modality": s.modality,
                    "in $/Mtok": s.input_cost_per_mtok,
                    "out $/Mtok": s.output_cost_per_mtok,
                }
                for s in registry.specs()
            ],
            ["alias", "provider", "model", "modality", "in $/Mtok", "out $/Mtok"],
        )
    )
    return 0


def cmd_prompts(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    prompts = settings.build_prompts()
    if not len(prompts):
        print("no prompts loaded (set WARNETECH_PROMPTS_DIR)", file=sys.stderr)
        return 1
    if args.id:
        template = prompts.get(args.id, args.version)
        print(json.dumps(template.to_dict(), indent=2))
        return 0
    print(
        _table(
            [
                {
                    "id": t.id,
                    "version": t.version,
                    "model": t.model or "-",
                    "checksum": t.checksum,
                    "variables": ",".join(t.variables) or "-",
                }
                for t in sorted(prompts.all(), key=lambda t: (t.id, t.version))
            ],
            ["id", "version", "model", "checksum", "variables"],
        )
    )
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    tracker = settings.build_usage()
    since = time.time() - args.hours * 3600 if args.hours else None
    summaries = tracker.summary(group_by=args.group_by, project=args.project, since=since)
    rows = [s.to_dict() for s in summaries]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(
        _table(
            [
                {
                    args.group_by: r["key"],
                    "calls": r["calls"],
                    "fail": r["failures"],
                    "tokens": r["total_tokens"],
                    "cost $": f"{r['cost_usd']:.6f}",
                    "avg ms": r["avg_latency_ms"],
                }
                for r in rows
            ],
            [args.group_by, "calls", "fail", "tokens", "cost $", "avg ms"],
        )
    )
    print(f"\ntotal: ${sum(r['cost_usd'] for r in rows):.6f}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    async def run() -> int:
        gateway, memory = build(project=args.project)
        try:
            context_text = ""
            if args.with_memory:
                result = await memory.get_context(args.project, args.question)
                context_text = result.text
                if result.hits:
                    print(f"(retrieved {len(result.hits)} memories)\n", file=sys.stderr)
            prompt = (
                f"<context>\n{context_text}\n</context>\n\n{args.question}"
                if context_text
                else args.question
            )
            response = await gateway.complete(args.model, prompt, project=args.project)
            print(response.text)
            print(
                f"\n-- {response.model_alias} via {response.provider} | "
                f"{response.usage.total_tokens} tokens | ${response.cost_usd:.6f} | "
                f"{response.latency_ms:.0f}ms",
                file=sys.stderr,
            )
            return 0
        finally:
            await memory.aclose()
            await gateway.aclose()

    return asyncio.run(run())


def cmd_remember(args: argparse.Namespace) -> int:
    async def run() -> int:
        gateway, memory = build(project=args.project)
        try:
            record = await memory.remember(args.project, args.text, key=args.key)
            print(f"stored {record.id} in {record.namespace}")
            return 0
        finally:
            await memory.aclose()
            await gateway.aclose()

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="warnetech", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("models", help="show the model registry and what aliases resolve to")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("prompts", help="list prompt templates, or show one")
    p.add_argument("id", nargs="?")
    p.add_argument("--version", type=int)
    p.set_defaults(func=cmd_prompts)

    p = sub.add_parser("usage", help="summarise token spend")
    p.add_argument(
        "--group-by",
        default="project",
        choices=["project", "team", "model_alias", "provider", "operation", "prompt_id"],
    )
    p.add_argument("--project")
    p.add_argument("--hours", type=float, default=24.0, help="0 for all time")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_usage)

    p = sub.add_parser("ask", help="one-shot completion, optionally grounded in memory")
    p.add_argument("question")
    p.add_argument("--model", default="chat.default")
    p.add_argument("--project", default="default")
    p.add_argument("--with-memory", action="store_true")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("remember", help="store one memory")
    p.add_argument("text")
    p.add_argument("--project", default="default")
    p.add_argument("--key")
    p.set_defaults(func=cmd_remember)

    # Command groups built on cli_core (each command capped at 4 parameters).
    from .ollama_commands import group as ollama_group

    ollama_group.mount(sub)

    from .interpreter_commands import group as interpreter_group

    interpreter_group.mount(sub)

    args = parser.parse_args(argv)
    if getattr(args, "command", None) == "ollama":
        return ollama_group.dispatch(args)
    if getattr(args, "command", None) == "interpreter":
        return interpreter_group.dispatch(args)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
