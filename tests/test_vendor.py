"""Vendored repositories: manifest integrity, safe lookup, and on-demand use.

These run against the real ``vendor/`` directory plus a few synthetic roots for
the failure cases. ``run_aicli`` starts the actual vendored aicli in a child
process; nothing here touches the network.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from warnetech_core.errors import ConfigError
from warnetech_core.integrations.vendor import (
    AICLI_MIN_PYTHON,
    aicli_source,
    available,
    manifest,
    run_aicli,
    vendor_path,
)
from warnetech_core.vendor_commands import group

REPO = Path(__file__).resolve().parent.parent
EXPECTED = {"claude-code-assist", "cli-ai-command-center-prd"}


# ---- the real vendor/ directory -----------------------------------------


def test_manifest_lists_both_repositories():
    assert set(manifest()) == EXPECTED


def test_every_entry_records_a_full_commit_and_source():
    for name, info in manifest().items():
        assert re.fullmatch(r"[0-9a-f]{40}", info["commit"]), name
        assert info["url"].startswith("https://github.com/warnetech/"), name
        assert info["ref"], name


def test_every_manifest_path_exists_and_is_under_vendor():
    for name in manifest():
        path = vendor_path(name)
        assert path.is_dir()
        assert path.is_relative_to(REPO / "vendor")


def test_all_listed_repositories_are_present():
    assert set(available()) == EXPECTED


def test_vendored_licence_is_kept():
    assert (vendor_path("claude-code-assist") / "LICENSE").is_file()


def test_vendored_tests_are_not_collected_by_our_suite():
    # `pytest` at the repo root must stay scoped to tests/, or the vendored
    # packages' own tests (and their dependencies) would run as ours.
    assert 'testpaths = ["tests"]' in (REPO / "pyproject.toml").read_text(encoding="utf-8")


# ---- lookup safety ------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "nope", "../..", "claude-code-assist/../..", "/etc"])
def test_unknown_or_traversing_names_are_refused(bad):
    with pytest.raises(ConfigError):
        vendor_path(bad)


def _root_with(tmp_path, monkeypatch, entries, make_dirs=()):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "vendor.json").write_text(json.dumps(entries), encoding="utf-8")
    for d in make_dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WARNETECH_REPO_ROOT", str(tmp_path))


def test_manifest_path_that_escapes_vendor_is_refused(tmp_path, monkeypatch):
    _root_with(tmp_path, monkeypatch, {"x": {"path": "../outside"}}, make_dirs=["outside"])
    with pytest.raises(ConfigError, match="leaves vendor/"):
        vendor_path("x")


def test_listed_but_missing_directory_is_reported(tmp_path, monkeypatch):
    _root_with(tmp_path, monkeypatch, {"x": {"path": "vendor/x"}})
    assert available() == []
    with pytest.raises(ConfigError, match="missing"):
        vendor_path("x")


def test_no_vendor_directory_means_nothing_available(tmp_path, monkeypatch):
    monkeypatch.setenv("WARNETECH_REPO_ROOT", str(tmp_path))
    assert manifest() == {}
    assert available() == []


def test_corrupt_manifest_is_a_config_error(tmp_path, monkeypatch):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "vendor.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("WARNETECH_REPO_ROOT", str(tmp_path))
    with pytest.raises(ConfigError):
        manifest()


def test_manifest_that_is_not_an_object_is_refused(tmp_path, monkeypatch):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "vendor.json").write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setenv("WARNETECH_REPO_ROOT", str(tmp_path))
    with pytest.raises(ConfigError):
        manifest()


# ---- using aicli on demand ----------------------------------------------


def test_aicli_source_holds_the_package():
    assert (aicli_source() / "aicli").is_dir()


needs_aicli_python = pytest.mark.skipif(
    sys.version_info < AICLI_MIN_PYTHON,
    reason="the bundled aicli requires Python 3.11+ (it imports tomllib)",
)


@needs_aicli_python
def test_run_aicli_executes_the_vendored_cli():
    result = run_aicli(["--version"])
    assert result.returncode == 0
    assert "0.2" in (result.stdout + result.stderr)


@needs_aicli_python
def test_run_aicli_reports_failure_without_raising():
    result = run_aicli(["definitely-not-a-command"])
    assert result.returncode != 0


# ---- CLI ----------------------------------------------------------------


def _parse(tail):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    group.mount(sub)
    return parser.parse_args(["vendor", *tail])


def test_vendor_list_shows_both(capsys):
    assert group.dispatch(_parse(["list"])) == 0
    out = capsys.readouterr().out
    for name in EXPECTED:
        assert name in out
    assert "MISSING" not in out


def test_vendor_path_prints_the_directory(capsys):
    assert group.dispatch(_parse(["path", "claude-code-assist"])) == 0
    assert capsys.readouterr().out.strip() == str(vendor_path("claude-code-assist"))


def test_vendor_path_unknown_name_fails_cleanly(capsys):
    assert group.dispatch(_parse(["path", "nope"])) == 1
    assert "unknown vendored repository" in capsys.readouterr().err


# ---- sync script --------------------------------------------------------


def _sync(*args):
    return subprocess.run(
        ["bash", str(REPO / "scripts" / "vendor_sync.sh"), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="the sync script is a bash script")
def test_sync_script_rejects_an_unknown_name_before_doing_anything():
    result = _sync("../../etc")
    assert result.returncode == 2
    assert "unknown vendored repository" in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="the sync script is a bash script")
def test_sync_script_requires_a_name():
    assert _sync().returncode != 0


def test_run_aicli_says_why_it_cannot_run_on_old_python(monkeypatch):
    # Runs on every Python version: the guard must refuse before starting a process.
    monkeypatch.setattr(sys, "version_info", (3, 10, 0, "final", 0))
    with pytest.raises(ConfigError, match=r"needs Python 3\.11 or newer; this is 3\.10"):
        run_aicli(["--version"])


def test_run_aicli_guard_allows_the_minimum_version(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (*AICLI_MIN_PYTHON, 0, "final", 0))
    started = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: started.append(a) or "ran")
    assert run_aicli(["--version"]) == "ran"
    assert started and started[0][0][1:4] == ["-P", "-m", "aicli"]


def test_run_aicli_ignores_a_planted_module_in_the_working_directory(monkeypatch, tmp_path):
    # Regression: `python -m` searches the cwd first. With -P it must not.
    (tmp_path / "aicli").mkdir()
    (tmp_path / "aicli" / "__init__.py").write_text("raise SystemExit('planted')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "version_info", (*AICLI_MIN_PYTHON, 0, "final", 0))
    started = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: started.append(a) or "ran")
    run_aicli(["--version"])
    assert "-P" in started[0][0]
