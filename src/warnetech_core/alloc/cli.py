"""``warnetech-alloc`` command line: scan, ingest and audit.

Exit codes: 0 ok, 1 audit found problems, 2 bad usage or rules file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .rules import Priority, load_rules
from .store import audit, ingest, scan

_DEFAULT_DEST = "data/alloc"


def _dest(args: argparse.Namespace) -> Path:
    return Path(args.dest or os.environ.get("WARNETECH_ALLOC_DIR", _DEFAULT_DEST))


def _rules(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    path = args.rules or os.environ.get("WARNETECH_ALLOC_RULES")
    return load_rules(Path(path) if path else None)


def _print_plan(items) -> None:  # type: ignore[no-untyped-def]
    for item in items:
        print(f"{item.priority.value}  tier{int(item.tier)}  {item.path}")
        for reason in item.reasons:
            print(f"        - {reason}")
    summary = ", ".join(f"{p.value}: {sum(1 for i in items if i.priority == p)}" for p in Priority)
    print(f"\n{len(items)} file(s). {summary}")


def cmd_scan(args: argparse.Namespace) -> int:
    rules = _rules(args)
    items = scan(Path(args.path), rules, time.time(), max_files=args.max_files)
    if args.json:
        print(json.dumps([i.__dict__ | {"priority": i.priority.value, "tier": int(i.tier)}
                          for i in items], indent=2))
    elif items:
        _print_plan(items)
    else:
        print("No files found.")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    rules = _rules(args)
    dest = _dest(args)
    items = ingest(Path(args.path), dest, rules, time.time(), apply=args.apply,
                   max_files=args.max_files)
    if not args.apply:
        print("DRY RUN: nothing copied. Re-run with --apply to copy and index.\n")
    if items:
        _print_plan(items)
    else:
        print("No new files to ingest.")
    if args.apply:
        print(f"Copied into {dest}. Source files were left untouched.")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    rules = _rules(args)
    report = audit(_dest(args), rules)
    print(f"Indexed records: {report.total}  corrupt index lines skipped: {report.corrupt_lines}")
    for tier, used in sorted(report.usage_bytes.items()):
        print(f"  tier{tier}: {used} bytes")
    for label, values in (("missing", report.missing), ("changed since ingest", report.changed)):
        for value in values:
            print(f"{label}: {value}")
    for tier, used in report.over_quota.items():
        print(f"over quota: tier{tier} uses {used} bytes")
    print("OK" if report.ok else "PROBLEMS FOUND")
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="warnetech-alloc",
                                     description="Classify and allocate files by priority.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--rules", help="YAML rules file (or WARNETECH_ALLOC_RULES)")
        p.add_argument("--max-files", type=int, default=10_000)

    p = sub.add_parser("scan", help="dry-run: show predicted priority and tier")
    p.add_argument("path")
    p.add_argument("--json", action="store_true")
    common(p)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("ingest", help="plan, or with --apply copy and index files")
    p.add_argument("path")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--dest", help="allocation directory (or WARNETECH_ALLOC_DIR)")
    common(p)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("audit", help="check index against disk and tier quotas")
    p.add_argument("--dest", help="allocation directory (or WARNETECH_ALLOC_DIR)")
    common(p)
    p.set_defaults(func=cmd_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:
        print(f"warnetech-alloc: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
