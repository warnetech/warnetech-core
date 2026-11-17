"""Allocation dashboard: page safety, read views, manual overrides, path guards. Offline."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from warnetech_core.alloc import store
from warnetech_core.alloc.rules import Rules
from warnetech_core.service import alloc_ui
from warnetech_core.service import app as app_module

TOKEN = "alloc-ui-token"


def seed(tmp_path: Path) -> tuple[Path, Path]:
    """Ingest one P1 text file and one P3 binary into a temp allocation dir and memory store."""
    src, dest, mem = tmp_path / "src", tmp_path / "alloc", tmp_path / "memory.jsonl"
    src.mkdir()
    (src / "plan.md").write_bytes(b"quokka migration plan")
    (src / "blob.xyz").write_bytes(b"\x00\x01binary")
    store.ingest(src, dest, Rules(), time.time(), apply=True, memory_path=mem)
    return dest, mem


def make_client(monkeypatch, tmp_path, *, token=None):
    dest, mem = seed(tmp_path)
    monkeypatch.setenv("WARNETECH_ALLOC_DIR", str(dest))
    monkeypatch.setenv("WARNETECH_RAG_STORE_PATH", str(mem))
    monkeypatch.delenv("WARNETECH_ALLOC_RULES", raising=False)
    monkeypatch.setenv("WARNETECH_OFFLINE", "1")
    monkeypatch.setenv("WARNETECH_MEMORY_BACKEND", "memory")
    monkeypatch.setenv("WARNETECH_USAGE_BACKEND", "memory")
    monkeypatch.delenv("WARNETECH_UI_ALLOWED_HOSTS", raising=False)
    if token:
        monkeypatch.setenv("WARNETECH_API_TOKEN", token)
        monkeypatch.delenv("WARNETECH_ALLOW_ANONYMOUS", raising=False)
    else:
        monkeypatch.delenv("WARNETECH_API_TOKEN", raising=False)
        monkeypatch.setenv("WARNETECH_ALLOW_ANONYMOUS", "1")
    return TestClient(app_module.app, base_url="http://localhost:8080"), dest


@pytest.fixture
def env(monkeypatch, tmp_path):
    client, dest = make_client(monkeypatch, tmp_path)
    with client as c:
        yield c, dest


@pytest.fixture
def secured(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path, token=TOKEN)
    with client as c:
        yield c


def sha_of(client, name: str) -> str:
    rows = client.get("/ui/api/alloc/files").json()["files"]
    return next(r["sha256"] for r in rows if r["name"] == name)


# --- page safety ---------------------------------------------------------------------------

def test_page_is_served_with_a_strict_csp(env):
    client, _ = env
    r = client.get("/ui/alloc")
    assert r.status_code == 200 and "Warnetech allocation" in r.text
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_csp_hashes_match_the_inline_script_and_style(env):
    client, _ = env
    r = client.get("/ui/alloc")
    for tag in ("script", "style"):
        body = re.search(rf"<{tag}>(.*?)</{tag}>", r.text, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in r.headers["content-security-policy"]


def test_page_has_no_external_requests_innerhtml_or_inline_handlers():
    html = alloc_ui.PAGE.read_text(encoding="utf-8")
    assert "innerHTML" not in html and "outerHTML" not in html
    assert "eval(" not in html and "document.write" not in html
    assert "http://" not in html and "https://" not in html
    assert not re.search(r"<[^>]+\sstyle\s*=", html)
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", html)


def test_foreign_host_is_refused_for_page_and_api(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    with client:
        bad = {"Host": "evil.example"}
        assert client.get("/ui/alloc", headers=bad).status_code == 403
        assert client.get("/ui/api/alloc/summary", headers=bad).status_code == 403
        assert client.post("/ui/api/alloc/override", headers=bad, json={}).status_code == 403


def test_api_needs_the_token_when_one_is_set(secured):
    assert secured.get("/ui/api/alloc/summary").status_code == 401
    ok = secured.get("/ui/api/alloc/summary", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200


# --- read views ----------------------------------------------------------------------------

def test_summary_counts_files_by_tier_and_priority(env):
    client, _ = env
    s = client.get("/ui/api/alloc/summary").json()
    assert s["total"] == 2
    assert s["priorities"] == {"P0": 0, "P1": 1, "P2": 0, "P3": 1}
    by_tier = {t["tier"]: t["files"] for t in s["tiers"]}
    assert by_tier == {0: 0, 1: 1, 2: 0, 3: 1}


def test_files_are_sorted_by_priority_and_filterable(env):
    client, _ = env
    rows = client.get("/ui/api/alloc/files").json()["files"]
    assert [r["priority"] for r in rows] == ["P1", "P3"]
    only = client.get("/ui/api/alloc/files", params={"priority": "P3"}).json()
    assert [r["name"] for r in only["files"]] == ["blob.xyz"]


def test_bad_priority_filter_and_limit_are_rejected(env):
    client, _ = env
    assert client.get("/ui/api/alloc/files", params={"priority": "P9"}).status_code == 422
    assert client.get("/ui/api/alloc/files", params={"limit": 0}).status_code == 422


def test_memory_search_returns_snippet_with_meta(env):
    client, _ = env
    hits = client.get("/ui/api/alloc/memory", params={"q": "quokka"}).json()["hits"]
    assert len(hits) == 1
    assert "quokka migration plan" in hits[0]["text"]
    assert hits[0]["meta"]["priority"] == "P1"


def test_memory_without_a_query_lists_newest_records(env):
    client, _ = env
    r = client.get("/ui/api/alloc/memory").json()
    assert r["total"] == 2 and len(r["hits"]) == 2


# --- overrides -----------------------------------------------------------------------------

def test_override_moves_the_file_and_the_latest_record_wins(env):
    client, dest = env
    digest = sha_of(client, "plan.md")
    r = client.post("/ui/api/alloc/override", json={"sha256": digest, "priority": "P0"})
    assert r.status_code == 200
    body = r.json()
    assert (body["priority"], body["tier"], body["override"]) == ("P0", 0, True)
    assert (dest / "tier0_hot").is_dir() and any((dest / "tier0_hot").iterdir())
    assert not any((dest / "tier1_warm").iterdir())
    rows = client.get("/ui/api/alloc/files").json()["files"]
    assert [r["priority"] for r in rows] == ["P0", "P3"]
    assert len(rows) == 2  # history is kept in the index but each file appears once
    assert len((dest / "index.jsonl").read_text().splitlines()) == 3


def test_audit_is_clean_after_an_override(env):
    client, _ = env
    digest = sha_of(client, "plan.md")
    client.post("/ui/api/alloc/override", json={"sha256": digest, "priority": "P2"})
    assert client.get("/ui/api/alloc/audit").json()["ok"] is True


def test_unknown_sha_is_409_and_bad_bodies_are_400(env):
    client, _ = env
    unknown = "0" * 64
    assert client.post("/ui/api/alloc/override",
                       json={"sha256": unknown, "priority": "P1"}).status_code == 409
    assert client.post("/ui/api/alloc/override",
                       json={"sha256": "short", "priority": "P1"}).status_code == 400
    assert client.post("/ui/api/alloc/override",
                       json={"sha256": unknown, "priority": "P7"}).status_code == 400
    assert client.post("/ui/api/alloc/override", content=b"not json").status_code == 400


def test_non_hex_sha_is_refused(env):
    client, _ = env
    r = client.post("/ui/api/alloc/override", json={"sha256": "z" * 64, "priority": "P1"})
    assert r.status_code == 400


def test_override_never_moves_a_file_outside_the_allocation_dir(env, tmp_path):
    client, dest = env
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    evil = {"source": "x", "dest": "../outside.txt", "sha256": "a" * 64, "size": 7,
            "priority": "P1", "tier": 1, "reasons": [], "ingested_at": time.time()}
    with (dest / "index.jsonl").open("a") as handle:
        handle.write(json.dumps(evil) + "\n")
    r = client.post("/ui/api/alloc/override", json={"sha256": "a" * 64, "priority": "P0"})
    assert r.status_code == 409
    assert outside.read_text() == "keep me"


def test_audit_endpoint_reports_a_missing_file(env):
    client, dest = env
    next((dest / "tier1_warm").iterdir()).unlink()
    a = client.get("/ui/api/alloc/audit").json()
    assert a["ok"] is False and len(a["missing"]) == 1


def test_existing_ui_still_works_alongside(env):
    client, _ = env
    assert client.get("/ui").status_code == 200
    assert client.get("/health").status_code == 200
    assert os.environ["WARNETECH_ALLOC_DIR"]
