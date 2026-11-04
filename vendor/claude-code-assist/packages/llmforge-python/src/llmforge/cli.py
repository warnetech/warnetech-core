"""The ``llmforge`` command.

Four subcommands, each a thin wrapper over a library function so the CLI can
never drift from what the API does:

    llmforge doctor              self-test; reports rather than raises
    llmforge assure              run the gates over a diff on stdin or from git
    llmforge context <query>     show what would be packed, and why
    llmforge retention <path>    apply a retention policy to a JSONL log

Exit codes are the contract: 0 means clean, 1 means findings, 2 means the
command itself could not run. That distinction matters in CI -- "the gate found
something" and "the gate crashed" call for different responses.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def _doctor(args: argparse.Namespace) -> int:
    from .doctor import doctor, render

    report = doctor(live=args.live, model=args.model)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return EXIT_OK if report["ok"] else EXIT_FINDINGS


def _read_diff(args: argparse.Namespace) -> str:
    """The diff to check: stdin if piped, otherwise git."""
    if not sys.stdin.isatty():
        piped = sys.stdin.read()
        if piped.strip():
            return piped
    for command in (["git", "diff", "HEAD"], ["git", "diff", "--cached"], ["git", "diff"]):
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.stdout.strip():
            return result.stdout
    return ""


def _assure(args: argparse.Namespace) -> int:
    from .assurance import complexity_budget, diff_discipline

    diff = _read_diff(args)
    if not diff.strip():
        print("assurance: nothing to check (no diff on stdin and no changes in git)")
        return EXIT_OK

    verdict = diff_discipline(
        diff,
        request_terms=args.term or (),
        allowed_paths=args.path or None,
    )
    added = "\n".join(
        line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    verdict = verdict + complexity_budget(
        added, max_lines=args.max_lines, baseline_lines=args.baseline_lines
    )

    if args.json:
        print(
            json.dumps(
                {
                    "ok": verdict.passed,
                    "checked": verdict.checked,
                    "violations": [
                        {
                            "code": v.code,
                            "severity": v.severity,
                            "principle": v.principle,
                            "message": v.message,
                            "location": v.location,
                            "remedy": v.remedy,
                        }
                        for v in verdict.violations
                    ],
                },
                indent=2,
            )
        )
    else:
        print(verdict.report())
        if not args.term and not args.path:
            print(
                "\nnote: no --term or --path given, so scope checks were skipped. "
                "Pass the nouns from the request to make traceability checkable."
            )
    return EXIT_OK if verdict.passed else EXIT_FINDINGS


def _context(args: argparse.Namespace) -> int:
    from .context import build_context

    packed = build_context(
        args.query,
        args.root,
        budget_tokens=args.budget,
        pinned=args.pin or (),
    )
    if args.json:
        print(
            json.dumps(
                {
                    "tokens": packed.tokens,
                    "budget": packed.budget,
                    "utilization": round(packed.utilization, 3),
                    "included": packed.included,
                    "elided": packed.elided,
                },
                indent=2,
            )
        )
        return EXIT_OK

    print(
        f"context for {args.query!r}: {packed.tokens}/{packed.budget} tokens "
        f"({packed.utilization:.0%})"
    )
    for path in packed.included:
        print(f"  included  {path}")
    for path in packed.elided[:20]:
        print(f"  elided    {path}")
    if len(packed.elided) > 20:
        print(f"  ... and {len(packed.elided) - 20} more elided")
    return EXIT_OK


def _retention(args: argparse.Namespace) -> int:
    from .retention import AUDIT_TIERS, DEFAULT_TIERS, sweep_audit, sweep_jsonl

    path = Path(args.path)
    if not path.exists():
        print(f"retention: {path} does not exist", file=sys.stderr)
        return EXIT_ERROR

    if args.audit:
        report = sweep_audit(path, archive=args.archive, dry_run=args.dry_run)
        tiers = AUDIT_TIERS
    else:
        report = sweep_jsonl(path, archive=args.archive, dry_run=args.dry_run)
        tiers = DEFAULT_TIERS

    prefix = "would apply" if args.dry_run else "applied"
    print(f"{prefix}: {report.render()}")
    # Nested same-quote f-strings are a syntax error before Python 3.12.
    described = ", ".join(f"{t.name}<={t.max_age_days if t.max_age_days else 'inf'}d" for t in tiers)
    print(f"tiers: {described}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmforge", description=__doc__.split("\n")[0])
    parser.add_argument("--version", action="version", version=f"llmforge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_cmd = sub.add_parser("doctor", help="self-test; reports rather than raises")
    doctor_cmd.add_argument("--live", action="store_true", help="add one real API call")
    doctor_cmd.add_argument("--model", default="claude-haiku-4-5", help="model for --live")
    doctor_cmd.add_argument("--json", action="store_true")
    doctor_cmd.set_defaults(func=_doctor)

    assure_cmd = sub.add_parser("assure", help="run the assurance gates over a diff")
    assure_cmd.add_argument("--term", action="append", help="a noun from the request (repeatable)")
    assure_cmd.add_argument("--path", action="append", help="an authorized path (repeatable)")
    assure_cmd.add_argument("--max-lines", type=int, default=None)
    assure_cmd.add_argument("--baseline-lines", type=int, default=None)
    assure_cmd.add_argument("--json", action="store_true")
    assure_cmd.set_defaults(func=_assure)

    context_cmd = sub.add_parser("context", help="show what would be packed, and why")
    context_cmd.add_argument("query")
    context_cmd.add_argument("--root", default=".")
    context_cmd.add_argument("--budget", type=int, default=60_000)
    context_cmd.add_argument("--pin", action="append", help="always include this path")
    context_cmd.add_argument("--json", action="store_true")
    context_cmd.set_defaults(func=_context)

    retention_cmd = sub.add_parser("retention", help="apply a retention policy to a JSONL log")
    retention_cmd.add_argument("path")
    retention_cmd.add_argument("--audit", action="store_true", help="use audit tiers (archive, never compact)")
    retention_cmd.add_argument("--archive", default=None, help="write removed records here first")
    retention_cmd.add_argument("--dry-run", action="store_true")
    retention_cmd.set_defaults(func=_retention)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result: Any = args.func(args)
        return int(result)
    except KeyboardInterrupt:
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - a CLI reports, it does not traceback
        print(f"llmforge: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
