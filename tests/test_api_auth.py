"""Multi-user API surface: sessions, RBAC, keys, vault, control, monitor."""
from __future__ import annotations

import time

import pytest
from conftest import make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app

PASSWORD = "hunter2hunter2"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True))) as c:
        yield c


def _register(client, email="owner@example.com", name="Owner"):
    resp = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "name": name})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["token"], {"Authorization": f"Bearer {body['token']}"}, body["user"]


def _wait(client, run_id, headers, limit=200):
    body = {}
    for _ in range(limit):
        body = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if body["status"] in {"done", "failed", "cancelled"}:
            return body
        time.sleep(0.02)
    return body


def test_anonymous_is_rejected_but_health_is_open(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/metrics").status_code == 200
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/framework").status_code == 401


def test_register_login_logout(client):
    token, headers, user = _register(client)
    assert user["role"] == "owner"
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["identity"]["kind"] == "session" and me["user"]["email"] == "owner@example.com"
    assert client.post("/api/auth/logout", headers=headers).json()["logged_out"] is True
    assert client.get("/api/auth/me", headers=headers).status_code == 401

    login = client.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert login.status_code == 200 and login.json()["token"].startswith("of_")
    assert client.post("/api/auth/login", json={"email": "owner@example.com",
                                                "password": "wrongpassword"}).status_code == 401


def test_second_user_is_member_and_cannot_manage(client):
    _register(client)
    token2, headers2, user2 = _register(client, "member@example.com", "Member")
    assert user2["role"] == "member"
    assert client.get("/api/runs", headers=headers2).status_code == 200
    assert client.post("/api/runs", json={"goal": "Write a test plan"}, headers=headers2).status_code == 201
    assert client.get("/api/keys", headers=headers2).status_code == 200
    # members cannot mint keys or open the audit log
    assert client.post("/api/keys", json={"name": "nope"}, headers=headers2).status_code == 403
    assert client.get("/api/audit", headers=headers2).status_code == 403


def test_api_key_lifecycle(client):
    _, headers, _ = _register(client)
    created = client.post("/api/keys", json={"name": "ci"}, headers=headers).json()
    key = created["key"]
    assert key.startswith("ofk_") and created["record"]["prefix"] == key[:12]
    key_headers = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/runs", headers=key_headers).status_code == 200
    keys = client.get("/api/keys", headers=headers).json()
    assert keys[0]["prefix"] == key[:12] and "key" not in keys[0]
    assert client.delete(f"/api/keys/{keys[0]['id']}", headers=headers).json()["revoked"] == 1
    assert client.get("/api/runs", headers=key_headers).status_code == 401


def test_password_change_and_account_deletion(client):
    _, headers, _ = _register(client)
    assert client.post("/api/auth/password", json={"current_password": "wrongwrong1",
                                                   "new_password": "brandnewpass1"},
                       headers=headers).status_code == 403
    assert client.post("/api/auth/password", json={"current_password": PASSWORD,
                                                   "new_password": "brandnewpass1"},
                       headers=headers).status_code == 200
    # a second account exists, so deleting this one is allowed
    _register(client, "second@example.com", "Second")
    assert client.delete("/api/auth/me", headers=headers).json()["deleted"] == 1


def test_last_account_cannot_be_deleted(client):
    _, headers, _ = _register(client)
    assert client.delete("/api/auth/me", headers=headers).status_code == 409


def test_secrets_are_write_only(client):
    _, headers, _ = _register(client)
    assert client.post("/api/secrets", json={"name": "OPENAI_KEY", "value": "sk-live-123"},
                       headers=headers).status_code == 201
    listing = client.get("/api/secrets", headers=headers).json()
    assert listing["vault"]["backend"] == "fernet"
    assert [s["name"] for s in listing["secrets"]] == ["OPENAI_KEY"]
    assert "sk-live-123" not in str(listing)
    assert client.delete("/api/secrets/OPENAI_KEY", headers=headers).json()["deleted"] == 1


def test_workspace_invites_and_membership_checks(client):
    _, headers, _ = _register(client)
    _register(client, "guest@example.com", "Guest")
    workspace = client.post("/api/workspaces", json={"name": "Studio"}, headers=headers).json()
    assert workspace["id"].startswith("ws_")
    invite = client.post(f"/api/workspaces/{workspace['id']}/members",
                         json={"email": "guest@example.com", "role": "viewer"}, headers=headers)
    assert invite.status_code == 201 and invite.json()["role"] == "viewer"
    detail = client.get(f"/api/workspaces/{workspace['id']}", headers=headers).json()
    assert len(detail["members"]) == 2
    assert client.post(f"/api/workspaces/{workspace['id']}/members",
                       json={"email": "nobody@example.com", "role": "member"},
                       headers=headers).status_code == 404
    assert client.post(f"/api/workspaces/{workspace['id']}/members",
                       json={"email": "guest@example.com", "role": "owner"},
                       headers=headers).status_code == 400


def test_runs_carry_priority_and_checkpoints(client):
    _, headers, _ = _register(client)
    run = client.post("/api/runs", json={"goal": "Build a mobile puzzle game", "auto_approve": True,
                                         "priority": 8}, headers=headers).json()
    assert run["priority"] == 8
    body = _wait(client, run["id"], headers)
    assert body["status"] == "done"
    checkpoint = client.get(f"/api/runs/{run['id']}/checkpoint", headers=headers).json()
    assert checkpoint["seq"] >= 2 and checkpoint["plan"]
    resumed = client.post(f"/api/runs/{run['id']}/resume", headers=headers)
    assert resumed.status_code in (202, 409)


def test_idempotency_key_over_http(client):
    _, headers, _ = _register(client)
    payload = {"goal": "Write a test plan", "auto_approve": True, "idempotency_key": "abc-123"}
    first = client.post("/api/runs", json=payload, headers=headers).json()
    second = client.post("/api/runs", json=payload, headers=headers).json()
    assert first["id"] == second["id"]


def test_control_endpoints(client):
    _, headers, _ = _register(client)
    assert client.get("/api/control", headers=headers).json()["paused"] is False
    assert client.post("/api/control/pause", headers=headers).json()["paused"] is True
    assert client.post("/api/control/unpause", headers=headers).json()["paused"] is False
    assert client.post("/api/control/stop", headers=headers).json()["stopped"] is True
    blocked = client.post("/api/runs", json={"goal": "Write a test plan"}, headers=headers)
    assert blocked.status_code == 409
    assert client.post("/api/control/clear", headers=headers).json()["stopped"] is False
    assert client.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True},
                       headers=headers).status_code == 201


def test_monitor_reports_agents_and_tasks(client):
    _, headers, _ = _register(client)
    run = client.post("/api/runs", json={"goal": "Build a mobile game", "auto_approve": True},
                      headers=headers).json()
    _wait(client, run["id"], headers)
    monitor = client.get("/api/monitor", headers=headers).json()
    assert monitor["agents"]["total"] == 120
    assert monitor["tasks"].get("done", 0) >= 1
    assert "integrations" in monitor and "storage" in monitor


def test_audit_trail_records_actions(client):
    _, headers, _ = _register(client)
    client.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True}, headers=headers)
    actions = {entry["action"] for entry in client.get("/api/audit", headers=headers).json()}
    assert {"user.registered", "auth.login", "run.created"} <= actions


def test_retention_purge(client):
    _, headers, _ = _register(client)
    assert client.get("/api/retention", headers=headers).json()["storage"]["runs"] >= 0
    assert client.post("/api/retention/purge", headers=headers).json()["sessions_removed"] >= 0


def test_rate_limit_returns_429(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, rate_limit_per_minute=1,
                                             rate_limit_burst=1))) as c:
        assert c.get("/api/health").status_code == 200
        limited = c.get("/api/health")
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers


def test_viewer_role_cannot_run(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True))) as c:
        _, owner_headers, _ = _register(c)
        _register(c, "viewer@example.com", "Viewer")
        store = c.app.state.store
        viewer = store.get_user_by_email("viewer@example.com")
        store._conn.execute("UPDATE users SET role='viewer' WHERE id=?", (viewer["id"],))
        store._conn.commit()
        login = c.post("/api/auth/login", json={"email": "viewer@example.com", "password": PASSWORD})
        viewer_headers = {"Authorization": f"Bearer {login.json()['token']}"}
        assert c.get("/api/runs", headers=viewer_headers).status_code == 200
        assert c.post("/api/runs", json={"goal": "Write a test plan"},
                      headers=viewer_headers).status_code == 403
