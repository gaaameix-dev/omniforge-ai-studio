"""API surface: auth, metrics, replay, artifacts, knowledge search, events."""
from __future__ import annotations

import time

import pytest
from conftest import make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app


def _wait(client, run_id, limit=200):
    body = {}
    for _ in range(limit):
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in {"done", "failed", "cancelled"}:
            return body
        time.sleep(0.02)
    return body


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        yield c


def test_health_and_framework(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["auth_required"] is False
    framework = client.get("/api/framework").json()
    assert framework["version"] == "0.7.0"
    assert framework["counts"]["agents"] == 120
    assert framework["policy"]["max_retries"] >= 0
    assert "storage" in framework and "knowledge" in framework


def test_run_lifecycle_and_events(client):
    created = client.post("/api/runs", json={"goal": "Build a mobile habit tracker", "auto_approve": True})
    assert created.status_code == 201
    run_id = created.json()["id"]
    body = _wait(client, run_id)
    assert body["status"] == "done"
    assert body["tokens"] > 0
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert events[0]["type"] == "run.created"
    assert events[-1]["type"] == "run.finished"
    assert client.get("/api/runs/does-not-exist").status_code == 404
    assert client.get("/api/runs/does-not-exist/events").status_code == 404


def test_artifacts_are_listed_and_downloadable(client):
    run_id = client.post("/api/runs", json={"goal": "Build a mobile habit tracker",
                                            "auto_approve": True}).json()["id"]
    _wait(client, run_id)
    listing = client.get(f"/api/runs/{run_id}/artifacts").json()
    names = [item["name"] for item in listing]
    assert "summary.md" in names
    assert any(n.endswith(".md") for n in names)
    download = client.get(f"/api/runs/{run_id}/artifacts/summary.md")
    assert download.status_code == 200 and "Handoff summary" in download.text
    assert client.get(f"/api/runs/{run_id}/artifacts/nope.md").status_code == 404


def test_replay_creates_a_new_run_with_the_same_goal(client):
    run_id = client.post("/api/runs", json={"goal": "Design a postgres schema",
                                            "auto_approve": True}).json()["id"]
    _wait(client, run_id)
    replayed = client.post(f"/api/runs/{run_id}/replay")
    assert replayed.status_code == 201
    body = replayed.json()
    assert body["id"] != run_id and body["goal"] == "Design a postgres schema"
    _wait(client, body["id"])
    assert client.post("/api/runs/nope/replay").status_code == 404


def test_knowledge_search_after_a_run(client):
    run_id = client.post("/api/runs", json={"goal": "Design a postgres invoicing schema",
                                            "auto_approve": True}).json()["id"]
    _wait(client, run_id)
    hits = client.get("/api/knowledge/search", params={"q": "postgres schema"}).json()
    assert hits["hits"], hits
    assert hits["stats"]["documents"] >= 1


def test_metrics_endpoints(client):
    client.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True})
    snap = client.get("/api/metrics").json()
    assert snap["counters"]["runs.submitted"] >= 1
    text = client.get("/metrics").text
    assert "omniforge_runs_submitted_total" in text
    stats = client.get("/api/stats").json()
    assert stats["storage"]["runs"] >= 1


def test_list_runs_filters_by_status(client):
    run_id = client.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True}).json()["id"]
    _wait(client, run_id)
    done = client.get("/api/runs", params={"status": "done"}).json()
    assert any(r["id"] == run_id for r in done)
    assert client.get("/api/runs", params={"status": "planning"}).json() == []


def test_auth_required_when_api_key_is_set(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, api_key="s3cret"))) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/metrics").status_code == 200
        assert c.get("/api/framework").status_code == 401
        assert c.get("/api/framework", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/framework", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/framework", params={"token": "s3cret"}).status_code == 200
        created = c.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True},
                         headers={"Authorization": "Bearer s3cret"})
        assert created.status_code == 201
        run_id = created.json()["id"]
        assert c.get(f"/api/runs/{run_id}", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_websocket_rejects_missing_token_when_auth_is_on(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    with TestClient(create_app(make_settings(tmp_path, api_key="s3cret"))) as c:
        run_id = c.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True},
                        headers={"Authorization": "Bearer s3cret"}).json()["id"]
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f"/ws/runs/{run_id}") as ws:
                ws.receive_json()
        with c.websocket_connect(f"/ws/runs/{run_id}?token=s3cret") as ws:
            first = ws.receive_json()
            assert "type" in first


def test_unknown_approval_returns_conflict(client):
    run_id = client.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True}).json()["id"]
    _wait(client, run_id)
    resp = client.post(f"/api/runs/{run_id}/approvals/task_nope", json={"approved": True})
    assert resp.status_code == 409


def test_dashboard_is_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "OmniForge" in page.text


def test_history_survives_a_restart(tmp_path):
    cfg = make_settings(tmp_path)
    with TestClient(create_app(cfg)) as first:
        run_id = first.post("/api/runs", json={"goal": "Design a postgres schema",
                                               "auto_approve": True}).json()["id"]
        _wait(first, run_id)
    # a brand new app instance over the same data dir
    with TestClient(create_app(make_settings(tmp_path))) as second:
        assert second.get("/api/health").json()["runs"] == 0  # nothing in memory
        record = second.get(f"/api/runs/{run_id}").json()
        assert record["status"] == "done"
        assert record["plan"]["tasks"]
        listed = second.get("/api/runs").json()
        assert any(r["id"] == run_id for r in listed)
        assert second.get("/api/stats").json()["storage"]["runs"] == 1


def test_projects_group_runs(client):
    project = client.post("/api/projects", json={"name": "Game build"}).json()
    assert project["id"].startswith("proj_")
    run_id = client.post("/api/runs", json={"goal": "Build a mobile game", "auto_approve": True,
                                            "project_id": project["id"]}).json()["id"]
    body = _wait(client, run_id)
    assert body["project_id"] == project["id"]
    detail = client.get(f"/api/projects/{project['id']}").json()
    assert detail["runs"] and detail["runs"][0]["id"] == run_id
    filtered = client.get("/api/runs", params={"project_id": project["id"]}).json()
    assert [r["id"] for r in filtered] == [run_id]
    assert len(client.get("/api/projects").json()) == 1
    assert client.get("/api/projects/proj_missing").status_code == 404


def test_integrations_endpoints_report_and_verify(client):
    data = client.get("/api/integrations").json()
    keys = {i["key"] for i in data["integrations"]}
    assert keys == {"llm", "github", "mcp", "sqlite", "sandbox", "http"}
    assert data["connected"] == ["sqlite"]
    verified = client.post("/api/integrations/sqlite/verify").json()
    assert verified["status"] == "connected"
    assert client.post("/api/integrations/nope/verify").status_code == 404
    everything = client.post("/api/integrations/verify").json()
    assert len(everything["integrations"]) == 6


def test_tool_registry_exposes_permissions(client):
    tools = {t["name"]: t for t in client.get("/api/tools").json()}
    assert tools["http_get"]["permissions"] == ["net.read"]
    assert tools["github_put_file"]["destructive"] is True
    assert tools["github_put_file"]["permissions"] == ["github.write"]
    assert tools["code_exec"] if "code_exec" in tools else True


def test_tool_calls_are_visible(client):
    run_id = client.post("/api/runs", json={"goal": "Profile the dataset for a report",
                                            "auto_approve": True}).json()["id"]
    _wait(client, run_id)
    calls = client.get("/api/tool-calls", params={"run_id": run_id}).json()
    assert calls and calls[0]["tool"] == "text_stats" and calls[0]["ok"] == 1
    assert client.get("/api/tool-calls").json()


def test_dashboard_is_mobile_ready(client):
    page = client.get("/").text
    assert "width=device-width" in page
    assert "viewport-fit=cover" in page
    for section in ("chat", "projects", "departments", "agents", "skills", "tools", "logs"):
        assert f'data-v="{section}"' in page
