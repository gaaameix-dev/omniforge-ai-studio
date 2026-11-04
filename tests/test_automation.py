"""Workflows, webhooks, schedules and outbound notifications."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from conftest import make_orchestrator, make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core.notifier import Notifier
from omniforge.core.scheduler import Scheduler, compute_next

PASSWORD = "hunter2hunter2"


def _wait(client, run_id, headers, limit=200):
    body = {}
    for _ in range(limit):
        body = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if body["status"] in {"done", "failed", "cancelled"}:
            return body
        time.sleep(0.02)
    return body


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, scheduler_enabled=False,
                                             auth_enabled=True))) as c:
        yield c


def _headers(client, email="owner@example.com"):
    body = client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD, "name": "Owner"}).json()
    return {"Authorization": f"Bearer {body['token']}"}


def _seed_run(client, headers) -> str:
    run = client.post("/api/runs", json={"goal": "Build a mobile puzzle game",
                                         "auto_approve": True}, headers=headers).json()
    _wait(client, run["id"], headers)
    return run["id"]


# ----------------------------------------------------------------- workflows
def test_workflow_from_run_and_replay(client):
    headers = _headers(client)
    run_id = _seed_run(client, headers)
    created = client.post(f"/api/workflows/from-run/{run_id}", json={"name": "Game build"},
                          headers=headers)
    assert created.status_code == 201
    workflow_id = created.json()["id"]
    detail = client.get(f"/api/workflows/{workflow_id}", headers=headers).json()
    assert detail["plan"]["tasks"] and detail["name"] == "Game build"

    replayed = client.post(f"/api/workflows/{workflow_id}/run", json={}, headers=headers)
    assert replayed.status_code == 201
    body = _wait(client, replayed.json()["id"], headers)
    assert body["status"] == "done"
    assert body["plan"]["tasks"]
    assert len(client.get("/api/workflows", headers=headers).json()) == 1


def test_workflow_parameter_substitution(client):
    headers = _headers(client)
    run_id = _seed_run(client, headers)
    workflow = client.post(f"/api/workflows/from-run/{run_id}", json={"name": "templated"},
                           headers=headers).json()
    detail = client.get(f"/api/workflows/{workflow['id']}", headers=headers).json()
    detail["plan"]["tasks"][0]["title"] = "Research {{topic}}"
    detail["plan"]["tasks"][0]["description"] = "Brief about {{topic}} for {{audience}}"
    store = client.app.state.store
    templated = store.save_workflow("templated", "Plan {{topic}} for {{audience}}",
                                    json.dumps(detail["plan"]), "[]",
                                    "2026-01-01T00:00:00+00:00")
    run = client.post(f"/api/workflows/{templated['id']}/run",
                      json={"params": {"topic": "logistics", "audience": "CFO"}},
                      headers=headers).json()
    body = _wait(client, run["id"], headers)
    titles = [t["title"] for t in body["plan"]["tasks"]]
    assert "Research logistics" in titles
    assert "logistics" in body["goal"] and "CFO" in body["goal"]


def test_workflow_validation_and_deletion(client):
    headers = _headers(client)
    assert client.post("/api/workflows", json={"name": "x", "goal_template": "g"},
                       headers=headers).status_code == 422
    empty = client.post("/api/workflows", json={"name": "empty", "goal_template": "do it",
                                                "plan": {"tasks": []}}, headers=headers)
    assert empty.status_code == 400
    run_id = _seed_run(client, headers)
    workflow = client.post(f"/api/workflows/from-run/{run_id}", json={"name": "to delete"},
                           headers=headers).json()
    assert client.delete(f"/api/workflows/{workflow['id']}", headers=headers).json()["deleted"] == 1
    assert client.get(f"/api/workflows/{workflow['id']}", headers=headers).status_code == 404


# --------------------------------------------------------------------- hooks
def test_hook_trigger_starts_a_workflow(client):
    headers = _headers(client)
    run_id = _seed_run(client, headers)
    workflow = client.post(f"/api/workflows/from-run/{run_id}", json={"name": "hooked"},
                           headers=headers).json()
    hook = client.post("/api/hooks", json={"workflow_id": workflow["id"]}, headers=headers).json()
    assert hook["token"].startswith("ofh_")
    assert "secret" in hook["warning"]

    triggered = client.post(f"/api/hooks/{hook['token']}/trigger", json={"params": {"a": 1}})
    assert triggered.status_code == 202
    body = _wait(client, triggered.json()["run_id"], headers)
    assert body["status"] == "done"
    assert client.post("/api/hooks/bogus/trigger", json={}).status_code == 404

    hooks = client.get("/api/hooks", headers=headers).json()
    assert hooks[0]["last_called_at"]
    assert client.delete(f"/api/hooks/{hooks[0]['id']}", headers=headers).json()["deleted"] == 1
    assert client.post(f"/api/hooks/{hook['token']}/trigger", json={}).status_code == 404


def test_hook_requires_manage_role(client):
    owner = _headers(client)
    _headers(client, "member@example.com")
    login = client.post("/api/auth/login", json={"email": "member@example.com",
                                                 "password": PASSWORD}).json()
    member = {"Authorization": f"Bearer {login['token']}"}
    run_id = _seed_run(client, owner)
    workflow = client.post(f"/api/workflows/from-run/{run_id}", json={"name": "member test"},
                           headers=owner).json()
    assert client.post("/api/hooks", json={"workflow_id": workflow["id"]},
                       headers=member).status_code == 403


# ----------------------------------------------------------------- schedules
def test_compute_next_interval_and_daily():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert compute_next({"kind": "interval", "every_seconds": 600}, now).startswith("2026-01-01T10:10")
    daily = compute_next({"kind": "daily", "at_hour": 9, "at_minute": 30}, now)
    assert daily.startswith("2026-01-02T09:30")
    later_today = compute_next({"kind": "daily", "at_hour": 23, "at_minute": 0}, now)
    assert later_today.startswith("2026-01-01T23:00")


def test_schedule_crud_and_manual_fire(client):
    headers = _headers(client)
    created = client.post("/api/schedules", json={"name": "nightly", "goal": "Write a test plan",
                                                  "kind": "interval", "every_seconds": 60},
                          headers=headers)
    assert created.status_code == 201
    schedule = created.json()
    assert schedule["next_run_at"]

    fired = client.post(f"/api/schedules/{schedule['id']}/run", headers=headers)
    assert fired.status_code == 202
    body = _wait(client, fired.json()["run_id"], headers)
    assert body["status"] == "done"

    assert client.post(f"/api/schedules/{schedule['id']}/disable",
                       headers=headers).json()["enabled"] is False
    assert client.post(f"/api/schedules/{schedule['id']}/enable",
                       headers=headers).json()["enabled"] is True
    assert client.delete(f"/api/schedules/{schedule['id']}", headers=headers).json()["deleted"] == 1
    assert client.post("/api/schedules", json={"name": "bad"}, headers=headers).status_code == 400


async def test_scheduler_tick_fires_due_schedules(tmp_path):
    orch = make_orchestrator(tmp_path)
    store = orch.store
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(timespec="seconds")
    schedule = store.create_schedule({
        "name": "due now", "goal": "Write a test plan", "kind": "interval",
        "every_seconds": 60, "created_at": past, "next_run_at": past,
    })
    engine = Scheduler(orch, store, tick_seconds=0)
    fired = await engine.tick_once()
    assert len(fired) == 1 and fired[0]["schedule_id"] == schedule["id"]
    assert fired[0]["run_id"]
    await orch.wait(fired[0]["run_id"])
    updated = store.get_schedule(schedule["id"])
    assert updated["last_run_id"] == fired[0]["run_id"]
    assert updated["next_run_at"] > past
    # nothing due on the next pass
    assert await engine.tick_once() == []


async def test_scheduler_disabled_schedule_is_skipped(tmp_path):
    orch = make_orchestrator(tmp_path)
    store = orch.store
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    schedule = store.create_schedule({"name": "off", "goal": "x", "created_at": past,
                                      "next_run_at": past})
    store.set_schedule_enabled(schedule["id"], False)
    engine = Scheduler(orch, store, tick_seconds=0)
    assert await engine.tick_once() == []


async def test_scheduler_fires_a_workflow(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.run_to_completion("Build a mobile game", auto_approve=True)
    checkpoint = orch.store.latest_checkpoint(run.id)
    workflow = orch.store.save_workflow("wf", "Plan {{x}}", checkpoint["plan"], "[]",
                                        "2026-01-01T00:00:00+00:00")
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(timespec="seconds")
    orch.store.create_schedule({"name": "wf schedule", "workflow_id": workflow["id"],
                                "created_at": past, "next_run_at": past})
    engine = Scheduler(orch, orch.store, tick_seconds=0)
    fired = await engine.tick_once()
    assert fired[0]["run_id"]
    await orch.wait(fired[0]["run_id"])
    assert orch.metrics.counters["workflows.run"] == 1


# --------------------------------------------------------------- notifier
async def test_notifier_delivers_and_reports():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200)

    notifier = Notifier("https://hooks.test/x", transport=httpx.MockTransport(handler))
    result = await notifier.send({"event": "run.finished", "run_id": "r1"})
    assert result["ok"] is True and result["attempts"] == 1
    assert seen[0]["run_id"] == "r1"


async def test_notifier_retries_then_gives_up():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500)

    notifier = Notifier("https://hooks.test/x", transport=httpx.MockTransport(handler),
                        max_attempts=3, base_delay=0.0)
    result = await notifier.send({"event": "x"})
    assert result["ok"] is False and len(attempts) == 3
    assert "500" in result["error"]


async def test_notifier_disabled():
    notifier = Notifier("")
    assert notifier.enabled is False
    result = await notifier.send({"event": "x"})
    assert result["ok"] is False and result["attempts"] == 0


async def test_run_finish_records_a_notification(tmp_path):
    from omniforge.core.notifier import Notifier

    calls = []
    notifier = Notifier("https://hooks.test/x",
                        transport=httpx.MockTransport(lambda r: (calls.append(1), httpx.Response(200))[1]))
    orch = make_orchestrator(tmp_path, notifier=notifier)
    run = await orch.run_to_completion("Write a test plan", auto_approve=True)
    assert calls, "the webhook should have been called"
    notifications = orch.store.list_notifications()
    assert notifications[0]["run_id"] == run.id and notifications[0]["ok"] == 1


def test_notification_test_endpoint_requires_a_webhook(client):
    headers = _headers(client)
    assert client.post("/api/notifications/test", headers=headers).status_code == 409
    assert client.get("/api/notifications", headers=headers).json() == []
