"""Local web UI: page, host/origin guard, ask with sources, uploads. Offline."""

import re

import pytest
from fastapi.testclient import TestClient

from warnetech_core.service import app as app_module
from warnetech_core.service import warnetech_ui as ui

TOKEN = "ui-test-token"


def make_client(monkeypatch, *, token=None):
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
    return TestClient(app_module.app, base_url="http://localhost:8080")


@pytest.fixture
def client(monkeypatch):
    with make_client(monkeypatch) as c:
        yield c


@pytest.fixture
def secured(monkeypatch):
    with make_client(monkeypatch, token=TOKEN) as c:
        yield c


def test_page_is_served_with_a_strict_csp(client):
    r = client.get("/ui")
    assert r.status_code == 200 and "Warnetech" in r.text
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_csp_hashes_match_the_inline_script_and_style(client):
    import base64
    import hashlib

    r = client.get("/ui")
    for tag in ("script", "style"):
        body = re.search(rf"<{tag}>(.*?)</{tag}>", r.text, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in r.headers["content-security-policy"]


def test_page_makes_no_external_requests_and_never_uses_innerhtml():
    html = ui.PAGE.read_text(encoding="utf-8")
    assert "innerHTML" not in html and "outerHTML" not in html
    assert "eval(" not in html and "document.write" not in html
    urls = re.findall(r"(?:src|href|action)\s*=\s*[\"']?(https?:)?//", html)
    assert not urls and "http://" not in html and "https://" not in html


def test_page_has_no_inline_style_attributes_or_event_handlers():
    # The CSP blocks both, so any of them would silently break in a real browser.
    html = ui.PAGE.read_text(encoding="utf-8")
    assert not re.search(r"<[^>]+\sstyle\s*=", html)
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", html)


def test_foreign_host_is_refused(monkeypatch):
    with make_client(monkeypatch) as c:
        for path in ("/ui", "/ui/api/info"):
            assert c.get(path, headers={"host": "evil.example"}).status_code == 403


def test_foreign_origin_is_refused(client):
    r = client.post("/ui/api/ask", json={"question": "hi"},
                    headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.post("/ui/api/ask", json={"question": "hi"},
                     headers={"origin": "http://localhost:8080"})
    assert ok.status_code == 200


def test_ipv6_and_127_hosts_are_allowed(client):
    assert client.get("/ui", headers={"host": "127.0.0.1:8080"}).status_code == 200
    assert client.get("/ui", headers={"host": "[::1]:8080"}).status_code == 200


def test_extra_allowed_hosts_come_from_the_environment(monkeypatch):
    with make_client(monkeypatch) as c:
        monkeypatch.setenv("WARNETECH_UI_ALLOWED_HOSTS", "my-box.lan")
        assert c.get("/ui", headers={"host": "my-box.lan:8080"}).status_code == 200


def test_api_needs_the_token_when_one_is_set(secured):
    assert secured.get("/ui/api/info").json()["auth_required"] is True
    assert secured.post("/ui/api/ask", json={"question": "hi"}).status_code == 401
    r = secured.post("/ui/api/ask", json={"question": "hi"},
                     headers={"authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_info_reports_no_auth_in_anonymous_mode(client):
    assert client.get("/ui/api/info").json()["auth_required"] is False


def test_upload_then_ask_returns_sources(client):
    up = client.post("/ui/api/upload", json={"name": "notes.md", "text": "The vault code is blue."})
    assert up.status_code == 200 and up.json() == {"name": "notes.md", "chunks": 1, "removed": 0}
    r = client.post("/ui/api/ask", json={"question": "vault code?"}).json()
    assert r["text"] and r["provider"] == "echo"
    assert any(s["source"] == "notes.md#0" and "blue" in s["text"] for s in r["sources"])


def test_reupload_updates_in_place(client):
    body = {"name": "a.txt", "text": "\n\n".join(f"para {i} " + "w " * 400 for i in range(6))}
    first = client.post("/ui/api/upload", json=body).json()["chunks"]
    assert first > 1
    again = client.post("/ui/api/upload", json={"name": "a.txt", "text": "short"}).json()
    assert again["chunks"] == 1 and again["removed"] == first - 1


@pytest.mark.parametrize("name,text,fragment", [
    (".env", "KEY=1", "file name"),
    ("api_secret.txt", "x", "secrets"),
    ("server.pem", "x", "secrets"),
    ("pic.png", "x", "supported"),
    ("a.txt", "ab\x00cd", "binary"),
    ("a.txt", "   ", "empty"),
    ("", "x", "name"),
])
def test_unsafe_uploads_are_refused(client, name, text, fragment):
    r = client.post("/ui/api/upload", json={"name": name, "text": text})
    assert r.status_code in (400, 422)
    if r.status_code == 400:
        assert fragment in r.json()["detail"]


def test_upload_name_is_reduced_to_a_safe_basename(client):
    r = client.post("/ui/api/upload", json={"name": "../../etc/notes.md", "text": "hello"})
    assert r.status_code == 200 and r.json()["name"] == "notes.md"
    assert ui.clean_upload_name("C:\\x\\y<z>.txt") == "y_z_.txt"


def test_oversized_text_and_body_are_refused(client):
    too_long = {"name": "big.txt", "text": "a" * (ui.MAX_UPLOAD_CHARS + 1)}
    assert client.post("/ui/api/upload", json=too_long).status_code == 400
    huge = b'{"name":"x.txt","text":"' + b"a" * ui.MAX_REQUEST_BYTES + b'"}'
    r = client.post("/ui/api/upload", content=iter([huge]),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


@pytest.mark.parametrize("body", [{"question": ""}, {"question": "x" * 4001}, {}, {"question": 5}])
def test_bad_questions_are_400(client, body):
    assert client.post("/ui/api/ask", json=body).status_code == 400


def test_bad_project_names_and_non_json_are_400(client):
    assert client.post("/ui/api/ask", json={"question": "hi", "project": "../x"}).status_code == 400
    assert client.post("/ui/api/ask", content=b"nope",
                       headers={"content-type": "application/json"}).status_code == 400


def test_existing_service_routes_still_work(client):
    assert client.get("/health").status_code == 200
