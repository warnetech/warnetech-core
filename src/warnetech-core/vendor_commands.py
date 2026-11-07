"""``warnetech vendor`` command group: see and locate the bundled repositories."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any

from .cli_core import Command, CommandGroup, Param
from .errors import ConfigError
from .integrations.vendor import available, manifest, vendor_path

group = CommandGroup("vendor", "repositories bundled under vendor/")


def _emit(text: str) -> None:
    sys.stdout.write(text + "\n")


def handle_list(_values: Mapping[str, Any]) -> int:
    entries = manifest()
    if not entries:
        _emit("no vendored repositories (vendor/vendor.json not found)")
        return 1
    present = set(available())
    _emit(f"{'NAME':<28} {'PRESENT':<8} {'COMMIT':<9} PROVIDES")
    for name, info in sorted(entries.items()):
        _emit(
            f"{name:<28} {'yes' if name in present else 'MISSING':<8} "
            f"{str(info.get('commit', ''))[:7]:<9} {info.get('provides', '')}"
        )
    return 0


def handle_path(values: Mapping[str, Any]) -> int:
    try:
        _emit(str(vendor_path(str(values["name"]))))
    except ConfigError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


group.register(
    Command("list", "show the bundled repositories and their source commits", handle_list)
)
group.register(
    Command(
        "path",
        "print the absolute path of a bundled repository",
        handle_path,
        (Param("name", help="manifest name, e.g. claude-code-assist"),),
    )
)
