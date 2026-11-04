"""Uploads, portable projects, agent scoring, memory, provenance and moderation."""
from __future__ import annotations

import io
import json
import time
import zipfile

import pytest
from conftest import make_orchestrator, make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core.moderation import Policy, scan
from omniforge.core.uploads import UploadError, safe_name, save_upload, type_allowed

PASSWORD = "hunter2hunter2"
# assembled at runtime so no key-shaped literal lives in the repo
FAKE_KEY = "sk-" + "a" * 24


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True,
                                             scheduler_enabled=False))) as c:
        yield c


def _headers(client, email="owner@example.com"):
    body = client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD, "name": "Owner"}).json()
    return {"Authorization": f"Bearer {body['token']}"}


def _wait(client, run_id, headers, limit=200):
    body = {}
    for _ in range(limit):
        body = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if body["status"] in {"done", "failed", "cancelled"}:
            return body
        time.sleep(0.02)
    return body


def _project(client, headers, name="Studio"):
    return client.post("/api/projects", json={"name": name}, headers=headers).json()


# ------------------------------------------------------------------- uploads
def test_safe_names_and_type_checks():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("a b/c?.txt") == "c_.txt"
    assert safe_name("") == "file"
    assert type_allowed("text/markdown")
    assert type_allowed("image/png")
    assert not type_allowed("application/x-msdownload")
    assert type_allowed("application/x-msdownload", ("application/",))


def test_save_upload_limits(tmp_path):
    saved = save_upload(tmp_path, b"hello", "notes.txt", "text/plain")
    assert saved["bytes"] == 5 and saved["sha256"]
    with pytest.raises(UploadError):
        save_upload(tmp_path, b"", "empty.txt", "text/plain")
    with pytest.raises(UploadError):
        save_upload(tmp_path, b"x" * 20, "big.bin", "text/plain", max_bytes=10)
    with pytest.raises(UploadError):
        save_upload(tmp_path, b"x", "bad.exe", "application/x-msdownload")


def test_upload_download_delete_roundtrip(client):
    headers = _headers(client)
    project = _project(client, headers)
    upload = client.post(f"/api/projects/{project['id']}/files",
                         files={"file": ("notes.md", io.BytesIO(b"# hello"), "text/markdown")},
                         headers=headers)
    assert upload.status_code == 201
    record = upload.json()
    assert record["name"] == "notes.md" and record["bytes"] == 7

    download = client.get(f"/api/files/{record['id']}/download", headers=headers)
    assert download.status_code == 200 and download.content == b"# hello"
    assert download.headers["x-content-sha256"] == record["sha256"]

    assert len(client.get(f"/api/files?project_id={project['id']}", headers=headers).json()) == 1
    assert client.delete(f"/api/files/{record['id']}", headers=headers).json()["deleted"] is True
    assert client.get(f"/api/files/{record['id']}/download", headers=headers).status_code == 404


def test_upload_to_missing_project(client):
    headers = _headers(client)
    resp = client.post("/api/projects/proj_nope/files",
                       files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")}, headers=headers)
    assert resp.status_code == 404


# ------------------------------------------------------- export and import
def test_export_import_roundtrip(client):
    headers = _headers(client)
    project = _project(client, headers, "Exportable")
    run = client.post("/api/runs", json={"goal": "Build a mobile game", "auto_approve": True,
                                         "project_id": project["id"]}, headers=headers).json()
    _wait(client, run["id"], headers)
    client.post(f"/api/projects/{project['id']}/files",
                files={"file": ("brief.txt", io.BytesIO(b"brief"), "text/plain")}, headers=headers)

    exported = client.get(f"/api/projects/{project['id']}/export", headers=headers)
    assert exported.status_code == 200 and exported.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as bundle:
        names = bundle.namelist()
        assert "project.json" in names
        assert any(n.startswith("artifacts/") for n in names)
        assert any(n.startswith("uploads/") for n in names)
        payload = json.loads(bundle.read("project.json"))
        assert payload["format"] == "omniforge-project/1"

    imported = client.post("/api/projects/import",
                           files={"file": ("p.zip", io.BytesIO(exported.content), "application/zip")},
                           headers=headers)
    assert imported.status_code == 201
    result = imported.json()
    assert result["runs"] and result["files"]
    new_project = client.get(f"/api/projects/{result['project_id']}", headers=headers).json()
    assert "(imported)" in new_project["name"]
    assert len(new_project["runs"]) == 1


def test_import_rejects_junk(client):
    headers = _headers(client)
    bad = client.post("/api/projects/import",
                      files={"file": ("x.zip", io.BytesIO(b"not a zip"), "application/zip")},
                      headers=headers)
    assert bad.status_code == 400
    empty_zip = io.BytesIO()
    with zipfile.ZipFile(empty_zip, "w") as bundle:
        bundle.writestr("hello.txt", "hi")
    empty_zip.seek(0)
    resp = client.post("/api/projects/import",
                       files={"file": ("x.zip", empty_zip, "application/zip")}, headers=headers)
    assert resp.status_code == 400 and "project.json" in resp.json()["detail"]


# --------------------------------------------------------- agent scoring
def test_agent_performance_ranks_agents(client):
    headers = _headers(client)
    run = client.post("/api/runs", json={"goal": "Build a mobile puzzle game",
                                         "auto_approve": True}, headers=headers).json()
    _wait(client, run["id"], headers)
    data = client.get("/api/agents/performance", headers=headers).json()
    assert data["agents"], data
    top = data["agents"][0]
    assert top["tasks"] >= 1 and top["name"]
    assert 0.0 <= top["reliability"] <= 1.0
    assert "avg_review_score" in top


# ---------------------------------------------------------------- memory
def test_memory_is_recorded_and_searchable(client):
    headers = _headers(client)
    run = client.post("/api/runs", json={"goal": "Design a postgres schema",
                                         "auto_approve": True}, headers=headers).json()
    _wait(client, run["id"], headers)
    entries = client.get(f"/api/memory?run_id={run['id']}", headers=headers).json()
    assert entries and entries[0]["agent_id"]
    hits = client.post("/api/memory/search", json={"query": "research"}, headers=headers).json()
    assert hits["scanned"] >= 1
    assert client.delete(f"/api/memory/{entries[0]['id']}", headers=headers).json()["deleted"] == 1
    cleared = client.delete(f"/api/memory/run/{run['id']}", headers=headers).json()
    assert cleared["deleted"] >= 0


# ------------------------------------------------------------ provenance
def test_artifacts_get_provenance_rows(client):
    headers = _headers(client)
    run = client.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True},
                      headers=headers).json()
    _wait(client, run["id"], headers)
    rows = client.get(f"/api/provenance?run_id={run['id']}", headers=headers).json()
    assert rows, "artifacts should be recorded"
    assert all(row["checksum"] for row in rows)
    assert rows[0]["licence"] == "generated:model-output"
    updated = client.post(f"/api/provenance/{rows[0]['id']}/licence",
                          json={"licence": "CC-BY-4.0"}, headers=headers)
    assert updated.status_code == 200
    assert client.get(f"/api/provenance?run_id={run['id']}",
                      headers=headers).json()[0]["licence"] == "CC-BY-4.0"


# ------------------------------------------------------------ moderation
def test_moderation_detects_secrets_and_pii():
    report = scan("key " + FAKE_KEY + " and mail me at a@b.co")
    rules = {f["rule"] for f in report.findings}
    assert {"openai_key", "email"} <= rules
    assert FAKE_KEY not in report.text
    assert "a@b.co" in report.text          # PII is flagged, not redacted by default


def test_blocked_terms_stop_a_goal(client):
    headers = _headers(client)
    store = client.app.state.store
    orch = client.app.state.orchestrator
    orch.policy = Policy(enabled=True, blocked_terms=("forbidden-topic",))
    response = client.post("/api/runs", json={"goal": "please do forbidden-topic now"},
                           headers=headers)
    assert response.status_code == 400
    assert "content policy" in response.json()["detail"]
    assert store.stats()["runs"] == 0


def test_moderation_endpoints(client):
    headers = _headers(client)
    policy = client.get("/api/moderation", headers=headers).json()
    assert policy["enabled"] is True and policy["rules"]
    result = client.post("/api/moderation/scan",
                         json={"text": "token " + FAKE_KEY}, headers=headers).json()
    assert result["clean"] is False
    assert "[redacted]" in result["redacted"]


async def test_goal_policy_blocks_before_any_model_call(tmp_path):
    orch = make_orchestrator(tmp_path)
    orch.policy = Policy(enabled=True, blocked_terms=("banned-word",))
    with pytest.raises(ValueError):
        await orch.submit("use banned-word in the build", auto_approve=True)
    assert orch.metrics.counters.get("runs.submitted", 0) == 0
