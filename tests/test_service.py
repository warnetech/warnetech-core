"""HTTP service tests. The app is driven through a real ASGI client."""


import pytest
from fastapi.testclient import TestClient

from warnetech_core.service import app as app_module

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WARNETECH_OFFLINE", "1")
    monkeypatch.setenv("WARNETECH_MEMORY_BACKEND", "memory")
    monkeypatch.setenv("WARNETECH_USAGE_BACKEND", "memory")
    monkeypatch.setenv("WARNETECH_API_TOKEN", TOKEN)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "greet.yaml").write_text(
        "id: greet\nversion: 1\nmodel: chat.default\n"
        "messages:\n  - role: user\n    content: 'Hello {{ name }}'\n"
    )
    monkeypatch.setenv("WARNETECH_PROMPTS_DIR", str(prompts))
    with TestClient(app_module.app) as test_client:
        yield test_client


def auth(**kwargs):
    return {"headers": {"Authorization": f"Bearer {TOKEN}"}, **kwargs}


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_protected_routes_require_a_token(client):
    assert client.get("/models").status_code == 401
    assert client.post("/v1/complete", json={"prompt": "hi"}).status_code == 401


def test_a_wrong_token_is_rejected(client):
    response = client.get("/models", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_service_refuses_to_start_unauthenticated(monkeypatch):
    monkeypatch.delenv("WARNETECH_API_TOKEN", raising=False)
    monkeypatch.delenv("WARNETECH_ALLOW_ANONYMOUS", raising=False)
    with pytest.raises(RuntimeError, match="WARNETECH_API_TOKEN"):
        with TestClient(app_module.app):
            pass


def test_anonymous_mode_must_be_opted_into(monkeypatch):
    monkeypatch.setenv("WARNETECH_OFFLINE", "1")
    monkeypatch.setenv("WARNETECH_MEMORY_BACKEND", "memory")
    monkeypatch.setenv("WARNETECH_USAGE_BACKEND", "memory")
    monkeypatch.delenv("WARNETECH_API_TOKEN", raising=False)
    monkeypatch.setenv("WARNETECH_ALLOW_ANONYMOUS", "1")
    with TestClient(app_module.app) as client:
        assert client.get("/models").status_code == 200


def test_complete_returns_text_and_cost(client):
    response = client.post("/v1/complete", json={"prompt": "hi", "project": "hub"}, **auth())
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "echo:hi"
    assert "cost_usd" in body and "usage" in body


def test_complete_requires_messages_or_prompt(client):
    assert client.post("/v1/complete", json={}, **auth()).status_code == 422


def test_unknown_model_is_a_404(client):
    response = client.post("/v1/complete", json={"prompt": "hi", "model": "nope"}, **auth())
    assert response.status_code == 404


def test_prompt_completion_renders_the_template(client):
    response = client.post(
        "/v1/complete/prompt",
        json={"prompt_id": "greet", "variables": {"name": "Ada"}},
        **auth(),
    )
    assert response.status_code == 200
    assert "Hello Ada" in response.json()["text"]


def test_missing_prompt_variable_is_a_400(client):
    response = client.post(
        "/v1/complete/prompt", json={"prompt_id": "greet", "variables": {}}, **auth()
    )
    assert response.status_code == 400
    assert "missing" in response.json()["detail"]


def test_unknown_prompt_is_a_404(client):
    response = client.post("/v1/complete/prompt", json={"prompt_id": "absent"}, **auth())
    assert response.status_code == 404


def test_prompt_listing_exposes_versions(client):
    assert client.get("/prompts", **auth()).json()["prompts"] == [
        {"id": "greet", "versions": [1]}
    ]


def test_models_endpoint_reports_availability(client):
    body = client.get("/models", **auth()).json()
    echo = next(m for m in body["models"] if m["alias"] == "echo.chat")
    assert echo["available"] is True
    assert body["aliases"]["chat.default"] == "echo.chat"


def test_embed_returns_one_vector_per_input(client):
    response = client.post("/v1/embed", json={"inputs": ["a", "b"]}, **auth())
    assert len(response.json()["vectors"]) == 2


def test_memory_round_trip_through_http(client):
    stored = client.post(
        "/v1/memory/remember",
        json={
            "project": "hub",
            "items": [{"text": "We chose Postgres for billing.", "key": "adr-1"}],
        },
        **auth(),
    )
    assert stored.json()["stored"] == 1

    context = client.post(
        "/v1/memory/context",
        json={"project": "hub", "query": "which database for billing"},
        **auth(),
    )
    body = context.json()
    assert "Postgres" in body["text"]
    assert body["namespaces"] == ["hub:default"]


def test_recall_does_not_cross_projects_by_default(client):
    client.post("/v1/memory/remember",
                json={"project": "a", "items": [{"text": "shared wording here"}]}, **auth())
    client.post("/v1/memory/remember",
                json={"project": "b", "items": [{"text": "shared wording here"}]}, **auth())
    hits = client.post("/v1/memory/recall",
                       json={"project": "a", "query": "shared wording"}, **auth()).json()["hits"]
    assert all(h["namespace"].startswith("a:") for h in hits)


def test_cross_project_recall_is_explicit(client):
    client.post("/v1/memory/remember",
                json={"project": "a", "items": [{"text": "shared wording here"}]}, **auth())
    client.post("/v1/memory/remember",
                json={"project": "b", "items": [{"text": "shared wording here"}]}, **auth())
    hits = client.post(
        "/v1/memory/recall",
        json={"project": "a", "query": "shared wording", "also_projects": ["b"]},
        **auth(),
    ).json()["hits"]
    assert {h["namespace"].split(":")[0] for h in hits} == {"a", "b"}


def test_listing_and_deleting_memory(client):
    client.post("/v1/memory/remember",
                json={"project": "hub", "items": [{"text": "x", "key": "k"}]}, **auth())
    assert client.get("/v1/memory/hub", **auth()).json()["count"] == 1
    assert client.delete("/v1/memory/hub?key=k", **auth()).json()["removed"] == 1
    assert client.get("/v1/memory/hub", **auth()).json()["count"] == 0


def test_delete_requires_a_selector(client):
    assert client.delete("/v1/memory/hub", **auth()).status_code == 422


def test_usage_summary_accumulates_across_calls(client):
    client.post("/v1/complete", json={"prompt": "one", "project": "hub"}, **auth())
    client.post("/v1/complete", json={"prompt": "two", "project": "hub"}, **auth())
    body = client.get("/v1/usage/summary?group_by=project", **auth()).json()
    assert next(r for r in body["rows"] if r["key"] == "hub")["calls"] == 2


def test_usage_summary_rejects_a_bad_grouping(client):
    assert client.get("/v1/usage/summary?group_by=evil", **auth()).status_code == 422
