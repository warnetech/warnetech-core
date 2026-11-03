"""Tests for core/engine.py bootstrap."""

from __future__ import annotations

from core.engine import _build_parser


class TestEngineParser:
    def test_ask_command(self):
        parser = _build_parser()
        args = parser.parse_args(["ask", "hello", "world"])
        assert args.command == "ask"
        assert args.prompt == ["hello", "world"]
        assert args.model == "chat.default"

    def test_run_command(self):
        parser = _build_parser()
        args = parser.parse_args(["run", "do", "something"])
        assert args.command == "run"
        assert args.goal == ["do", "something"]

    def test_status_command(self):
        parser = _build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_no_command(self):
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.command is None
