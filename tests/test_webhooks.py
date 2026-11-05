"""Signed webhooks: signature, freshness, replay, size, configuration. Offline."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from warnetech_core.service import app as app_module
from warnetech_core.service.webhooks import (
    MAX_BODY_BYTES,
    TOLERANCE_SECONDS,
    ReplayCache,
    configured,
    sign,
    verify,
)

SECRET = "s3cret-value-123456"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WARNETECH_OFFLINE", "1")
    monkeypatch.setenv("WARNETECH_MEMORY_BACKEND", "memory")
    monkeypatch.setenv("WARNETECH_USAGE_BACKEND", "memory")
    monkeypatch.setenv("WARNETECH_API_TOKEN", "t")
    monkeypatch.setenv("WARNETECH_WEBHOOKS", "ops,short")
    monkeypatch.setenv("WARNETECH_WEBHOOK_SECRET_OPS", SECRET)
    monkeypatch.setenv("WARNETECH_WEBHOOK_SECRET_SHORT", "tiny")
    with TestClient(app_module.app) as c:
        yield c


def post(client, body=None, *, name="ops", secret=SECRET, ts=None, sig=None, raw=None):
    raw = raw if raw is not None else json.dumps(body or {"text": "hello"}).encode()
    ts = str(int(time.time())) if ts is None else ts
    headers = {"X-Warnetech-Timestamp": ts,
               "X-Warnetech-Signature": sig or sign(secret, ts, raw),
               "Content-Type": "application/json"}
    return client.post(f"/v1/webhooks/{name}", content=raw, headers=headers)


def test_valid_request_gets_a_reply_without_the_bearer_token(client):
    r = post(client)
    assert r.status_code == 200 and r.json()["text"].startswith("echo:")


def test_wrong_secret_is_rejected(client):
    assert post(client, secret="x" * 20).status_code == 401


def test_tampered_body_is_rejected(client):
    ts = str(int(time.time()))
    good = sign(SECRET, ts, b'{"text": "hello"}')
    assert post(client, raw=b'{"text": "evil"}', ts=ts, sig=good).status_code == 401


def test_stale_and_future_timestamps_are_rejected(client):
    old = str(int(time.time()) - TOLERANCE_SECONDS - 5)
    future = str(int(time.time()) + TOLERANCE_SECONDS + 5)
    assert post(client, ts=old).status_code == 401
    assert post(client, ts=future).status_code == 401
    assert post(client, ts="abc").status_code == 401


def test_missing_headers_are_rejected(client):
    r = client.post("/v1/webhooks/ops", content=b"{}")
    assert r.status_code == 401


def test_replay_is_refused_and_does_not_call_the_model_again(client):
    raw = b'{"text": "once"}'
    ts = str(int(time.time()))
    sig = sign(SECRET, ts, raw)
    assert post(client, raw=raw, ts=ts, sig=sig).status_code == 200
    assert post(client, raw=raw, ts=ts, sig=sig).status_code == 409


def test_unknown_unconfigured_and_short_secret_names_are_404(client):
    assert post(client, name="nope").status_code == 404
    assert post(client, name="short", secret="tiny").status_code == 404


def test_oversized_body_is_413(client):
    big = b'{"text": "' + b"a" * MAX_BODY_BYTES + b'"}'
    assert post(client, raw=big).status_code == 413


@pytest.mark.parametrize("body", [{"text": ""}, {"text": 5}, {"nope": 1}, {"text": "x" * 9000}])
def test_bad_payloads_are_400(client, body):
    assert post(client, body).status_code == 400


def test_non_json_body_is_400(client):
    assert post(client, raw=b"not json").status_code == 400


def test_replay_cache_is_bounded_and_expires():
    cache = ReplayCache(capacity=3, ttl=10)
    for i in range(10):
        assert cache.check_and_add(f"k{i}", now=100.0)
    assert len(cache._seen) <= 3
    assert cache.check_and_add("k9", now=100.0) is False
    assert cache.check_and_add("k9", now=200.0) is True  # expired, so new again


def test_configured_reads_only_environment():
    env = {"WARNETECH_WEBHOOKS": "A-b, bad name,ok", "WARNETECH_WEBHOOK_SECRET_A_B": SECRET,
           "WARNETECH_WEBHOOK_SECRET_OK": "short", "WARNETECH_WEBHOOK_MODEL_A_B": "m.x"}
    assert configured(env) == {"a-b": (SECRET, "m.x")}


def test_verify_directly():
    body, ts = b"{}", "1000"
    assert verify(SECRET, ts, sign(SECRET, ts, body), body, now=1000) is None
    assert verify(SECRET, ts, "v1=00", body, now=1000) == "bad signature"


def test_oversized_body_without_content_length_is_413(client):
    big = b'{"text": "' + b"a" * MAX_BODY_BYTES + b'"}'
    ts = str(int(time.time()))
    r = client.post("/v1/webhooks/ops", content=iter([big]),  # chunked: no Content-Length
                    headers={"X-Warnetech-Timestamp": ts,
                             "X-Warnetech-Signature": sign(SECRET, ts, big)})
    assert r.status_code == 413
