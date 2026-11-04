"""Declarative command builder for the ``warnetech`` CLI.

Each command declares up to :data:`MAX_PARAMS` parameters. The cap is enforced
when the command is registered, so an over-long command fails at import time
rather than producing a confusing argparse error at the terminal.

A parameter is either positional (``required=True``, no flag) or an optional
``--flag``. Handlers receive a plain dict of parsed values and return an exit
code, which keeps them testable without argparse.
"""

from __future__ import annotations

import argparse
import functools
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

MAX_PARAMS = 4

Handler = Callable[[Mapping[str, Any]], int]


@dataclass(frozen=True)
class Param:
    name: str
    help: str = ""
    required: bool = True
    default: Any = None
    choices: tuple[str, ...] | None = None

    @property
    def is_flag(self) -> bool:
        """Optional parameters are exposed as ``--name`` flags."""
        return not self.required


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    handler: Handler
    params: tuple[Param, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if len(self.params) > MAX_PARAMS:
            raise ValueError(
                f"command {self.name!r} declares {len(self.params)} parameters; "
                f"the limit is {MAX_PARAMS}"
            )
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError(f"command {self.name!r} has duplicate parameter names")


class CommandGroup:
    """A named set of commands, mounted under one argparse subparser."""

    def __init__(self, name: str, help: str) -> None:
        self.name = name
        self.help = help
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> Command:
        if command.name in self._commands:
            raise ValueError(f"duplicate command {command.name!r} in group {self.name!r}")
        self._commands[command.name] = command
        return command

    def commands(self) -> list[Command]:
        return list(self._commands.values())

    def mount(self, subparsers: argparse._SubParsersAction) -> None:  # noqa: SLF001
        group_parser = subparsers.add_parser(self.name, help=self.help)
        group_sub = group_parser.add_subparsers(dest=f"{self.name}_command", required=True)
        for command in self._commands.values():
            cmd_parser = group_sub.add_parser(command.name, help=command.help)
            for param in command.params:
                _add_param(cmd_parser, param)
            cmd_parser.set_defaults(_command=command)

    def dispatch(self, namespace: argparse.Namespace, **context: Any) -> int:
        """Run the parsed command. ``context`` (e.g. an injected client) is merged
        into the handler's values; it is not a CLI parameter."""
        command: Command = namespace._command  # noqa: SLF001
        values: dict[str, Any] = {
            p.name: getattr(namespace, p.name.replace("-", "_")) for p in command.params
        }
        values.update(context)
        return command.handler(values)


def _add_param(parser: argparse.ArgumentParser, param: Param) -> None:
    dest = param.name.replace("-", "_")
    if param.is_flag:
        parser.add_argument(
            f"--{param.name}",
            dest=dest,
            default=param.default,
            choices=param.choices,
            help=param.help,
        )
    else:
        parser.add_argument(dest, help=param.help, choices=param.choices)


def report_config_errors(handler: Handler) -> Handler:
    """Wrap a handler so a configuration problem prints one line instead of a traceback."""
    from .errors import ConfigError

    @functools.wraps(handler)
    def wrapper(values: Mapping[str, Any]) -> int:
        try:
            return handler(values)
        except ConfigError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 1

    return wrapper
