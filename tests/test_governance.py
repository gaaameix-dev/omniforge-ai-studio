"""Project ACLs, project quotas, failure alerts, conditional workflows, load runs."""
from __future__ import annotations

import json
import time

import pytest
from conftest import make_orchestrator, make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core.loadtest import run_load
from omniforge.core.mailer import ConsoleMailer

PASSWORD = "hunter2hunter2"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True,
                                             scheduler_enabled=False))) as c:
        yield c


def _account(client, email="owner@example.com"):
    body = client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD, "name": email.split("@")[0]}).json()
    return {"Authorization": f"Bearer {body['token']}"}, body["user"]


def _wait(client, run_id, headers, limit=200):
    body = {}
    for _ in range(limit):
        body = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if body["status"] in {"done", "failed", "cancelled"}:
            return body
        time.sleep(0.02)
    return body


# ---------------------------------------------------------------- project ACL
def test_project_members_gate_access(client):
    owner, _ = _account(client)
    member, member_user = _account(client, "member@example.com")
    outsider, _ = _account(client, "outsider@example.com")

    project = client.post("/api/projects", json={"name": "Locked"}, headers=owner).json()
    # the creator is registered as project owner
    members = client.get(f"/api/projects/{project['id']}/members", headers=owner).json()
    assert members and members[0]["role"] == "owner"

    assert client.post(f"/api/projects/{project['id']}/members",
                       json={"email": "member@example.com", "role": "viewer"},
                       headers=owner).status_code == 201

    # member can read, outsider cannot
    assert client.get(f"/api/projects/{project['id']}", headers=member).status_code == 200
    assert client.get(f"/api/projects/{project['id']}", headers=outsider).status_code == 403
    assert client.get(f"/api/projects/{project['id']}/export",
                      headers=outsider).status_code == 403

    # a viewer may not start work in the project
    blocked = client.post("/api/runs", json={"goal": "Write a test plan",
                                             "project_id": project["id"]}, headers=member)
    assert blocked.status_code == 403

    # promoting to member allows running
    client.post(f"/api/projects/{project['id']}/members",
                json={"email": "member@example.com", "role": "member"}, headers=owner)
    allowed = client.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True,
                                             "project_id": project["id"]}, headers=member)
    assert allowed.status_code == 201
    _wait(client, allowed.json()["id"], member)

    # removing the membership closes the door again
    assert client.delete(f"/api/projects/{project['id']}/members/{member_user['id']}",
                         headers=owner).json()["removed"] == 1
    assert client.get(f"/api/projects/{project['id']}", headers=member).status_code == 403


def test_projects_without_members_stay_open(client):
    owner, _ = _account(client)
    other, _ = _account(client, "colleague@example.com")
    project = client.post("/api/projects", json={"name": "Open"}, headers=owner).json()
    store = client.app.state.store
    for row in store.list_project_members(project["id"]):
        store.remove_project_member(project["id"], row["user_id"])
    assert client.get(f"/api/projects/{project['id']}", headers=other).status_code == 200


def test_project_member_validation(client):
    owner, _ = _account(client)
    project = client.post("/api/projects", json={"name": "Checks"}, headers=owner).json()
    assert client.post(f"/api/projects/{project['id']}/members",
                       json={"email": "ghost@example.com", "role": "member"},
                       headers=owner).status_code == 404
    _account(client, "second@example.com")
    assert client.post(f"/api/projects/{project['id']}/members",
                       json={"email": "second@example.com", "role": "superuser"},
                       headers=owner).status_code == 400


# ------------------------------------------------------------ project quota
def test_project_quota_blocks_further_runs(client):
    owner, _ = _account(client)
    project = client.post("/api/projects", json={"name": "Budgeted", "token_quota": 1},
                          headers=owner).json()
    first = client.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True,
                                           "project_id": project["id"]}, headers=owner)
    assert first.status_code == 201
    _wait(client, first.json()["id"], owner)
    second = client.post("/api/runs", json={"goal": "Write another plan", "auto_approve": True,
                                            "project_id": project["id"]}, headers=owner)
    assert second.status_code == 402
    assert "quota" in second.json()["detail"]

    client.post(f"/api/projects/{project['id']}/quota", json={"tokens": 0}, headers=owner)
    third = client.post("/api/runs", json={"goal": "Write another plan", "auto_approve": True,
                                           "project_id": project["id"]}, headers=owner)
    assert third.status_code == 201


def test_project_quota_requires_manage(client):
    owner, _ = _account(client)
    member, _ = _account(client, "member@example.com")
    project = client.post("/api/projects", json={"name": "Guarded"}, headers=owner).json()
    client.post(f"/api/projects/{project['id']}/members",
                json={"email": "member@example.com", "role": "member"}, headers=owner)
    assert client.post(f"/api/projects/{project['id']}/quota", json={"tokens": 10},
                       headers=member).status_code == 403


# ------------------------------------------------------------------ alerts
async def test_failed_run_emails_the_operator(tmp_path):
    mailer = ConsoleMailer()
    orch = make_orchestrator(tmp_path, alert_email="ops@example.com", budget_tokens=1)
    orch.mailer = mailer
    run = await orch.run_to_completion("Build a mobile game", auto_approve=True)
    assert run.status.value == "failed"
    assert mailer.outbox, "a failure alert should have been queued"
    assert "failed" in mailer.outbox[-1].subject
    assert "Build a mobile game" in mailer.outbox[-1].body


async def test_no_alert_when_nothing_is_configured(tmp_path):
    orch = make_orchestrator(tmp_path, budget_tokens=1)
    mailer = ConsoleMailer()
    orch.mailer = mailer
    await orch.run_to_completion("Build a mobile game", auto_approve=True)
    assert mailer.outbox == []


# ------------------------------------------------------ conditional workflow
def _conditional_plan() -> dict:
    return {"goal": "Ship {{env}}", "tasks": [
        {"key": "a", "title": "Research", "department": "research",
         "skill": "data_profile", "depends_on": []},
        {"key": "b", "title": "Production hardening", "department": "qa",
         "skill": "test_plan", "depends_on": ["a"],
         "when": {"param": "env", "equals": "prod"}},
        {"key": "c", "title": "Handoff", "department": "product_ops",
         "skill": "product_spec", "depends_on": ["b"]},
    ]}


async def test_condition_drops_tasks_and_relinks_dependencies(tmp_path):
    orch = make_orchestrator(tmp_path)
    workflow = orch.store.save_workflow("cond", "Ship {{env}}",
                                        json.dumps(_conditional_plan()), "[]",
                                        "2026-01-01T00:00:00+00:00")
    dev = await orch.run_workflow(workflow, {"env": "dev"})
    await orch.wait(dev.id)
    titles = [t.title for t in dev.plan.tasks]
    assert titles == ["Research", "Handoff"]
    assert dev.status.value == "done"
    assert dev.meta["dropped_by_condition"] == ["b"]

    prod = await orch.run_workflow(workflow, {"env": "prod"})
    await orch.wait(prod.id)
    assert [t.title for t in prod.plan.tasks] == ["Research", "Production hardening", "Handoff"]


async def test_condition_with_in_and_exists(tmp_path):
    orch = make_orchestrator(tmp_path)
    plan = {"goal": "x", "tasks": [
        {"key": "a", "title": "Base", "department": "research", "skill": "data_profile",
         "depends_on": []},
        {"key": "b", "title": "Paid tier", "department": "qa", "skill": "test_plan",
         "depends_on": ["a"], "when": {"param": "tier", "in": ["pro", "enterprise"]}},
        {"key": "c", "title": "Needs notes", "department": "product_ops", "skill": "product_spec",
         "depends_on": ["a"], "when": {"param": "notes", "exists": True}},
    ]}
    workflow = orch.store.save_workflow("cond2", "x", json.dumps(plan), "[]",
                                        "2026-01-01T00:00:00+00:00")
    run = await orch.run_workflow(workflow, {"tier": "pro", "notes": "yes"})
    await orch.wait(run.id)
    assert {t.title for t in run.plan.tasks} == {"Base", "Paid tier", "Needs notes"}

    run2 = await orch.run_workflow(workflow, {"tier": "free"})
    await orch.wait(run2.id)
    assert {t.title for t in run2.plan.tasks} == {"Base"}


async def test_all_conditions_failing_is_an_error(tmp_path):
    orch = make_orchestrator(tmp_path)
    plan = {"goal": "x", "tasks": [
        {"key": "a", "title": "Never", "department": "qa", "skill": "test_plan",
         "depends_on": [], "when": {"param": "env", "equals": "prod"}},
    ]}
    workflow = orch.store.save_workflow("cond3", "x", json.dumps(plan), "[]",
                                        "2026-01-01T00:00:00+00:00")
    with pytest.raises(ValueError):
        await orch.run_workflow(workflow, {"env": "dev"})


# --------------------------------------------------------------- load runs
async def test_load_run_reports_latency(tmp_path):
    orch = make_orchestrator(tmp_path, max_parallel=4)
    result = await run_load(orch, runs=4, concurrency=2)
    assert result["runs"] == 4 and result["done"] == 4 and result["failed"] == 0
    assert result["latency"]["p50"] >= 0
    assert result["wall_time_s"] > 0
    assert result["throughput_runs_per_s"] > 0
    assert all(item["run_id"] for item in result["results"])
