"""PostgreSQL mirror, alert rules, plugin registry, LLM moderation, editor, sandbox."""
from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest
from conftest import make_orchestrator, make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core.alerts import AlertEngine
from omniforge.core.llm import MockLLM
from omniforge.core.mailer import ConsoleMailer
from omniforge.core.moderation import LlmModerator
from omniforge.core.plugin_registry import fetch_index, find, install_from_registry, parse_index
from omniforge.core.plugins import PluginError
from omniforge.core.store import RunStore

PASSWORD = "hunter2hunter2"


# --------------------------------------------------------------- PostgreSQL
@pytest.fixture(scope="module")
def pg_url():
    pgserver = pytest.importorskip("pgserver")
    pytest.importorskip("psycopg")
    import tempfile

    server = pgserver.get_server(tempfile.mkdtemp())
    return server.get_uri()


async def _seed_run(orch, goal="Write a test plan"):
    return await orch.run_to_completion(goal, auto_approve=True)


async def test_postgres_mirror_roundtrip(tmp_path, pg_url):
    from omniforge.core import pgmirror

    orch = make_orchestrator(tmp_path)
    await _seed_run(orch)
    result = pgmirror.sync(orch.store, pg_url)
    assert result["copied"]["runs"] >= 1
    assert result["copied"]["tasks"] >= 1
    assert result["copied"]["events"] >= 1
    report = pgmirror.verify(pg_url)
    assert report["ok"] is True
    assert report["rows"]["runs"] >= 1
    assert "PostgreSQL" in report["server"]

    # re-running is idempotent for runs/tasks
    again = pgmirror.sync(orch.store, pg_url)
    assert again["copied"]["runs"] >= 1
    assert pgmirror.verify(pg_url)["rows"]["runs"] == report["rows"]["runs"]


def test_pg_schema_has_every_mirrored_table():
    from omniforge.core import pg

    joined = " ".join(pg.schema_statements())
    for table in ("runs", "tasks", "events", "documents", "usage", "audit", "tool_calls",
                  "alert_rules"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in joined


# ------------------------------------------------------------- alert rules
def _engine(tmp_path, **kw):
    settings = make_settings(tmp_path, **kw)
    store = RunStore(settings.db_path)
    mailer = ConsoleMailer()
    engine = AlertEngine(store, settings, mailer=mailer)
    return engine, store, mailer, settings


def test_rule_validation(tmp_path):
    engine, store, _, _ = _engine(tmp_path)
    with pytest.raises(ValueError):
        engine.create(name="x", metric="nope", comparator="gt", threshold=1)
    with pytest.raises(ValueError):
        engine.create(name="x", metric="run_tokens", comparator="between", threshold=1)
    with pytest.raises(ValueError):
        engine.create(name="x", metric="run_tokens", comparator="gt", threshold=1, channel="sms")
    rule = engine.create(name="spend", metric="run_tokens", comparator="gt", threshold=10,
                         channel="email")
    assert store.get_alert_rule(rule["id"])["name"] == "spend"
    assert engine.delete(rule["id"]) == 1
    store.close()


async def test_failure_rule_fires_and_respects_cooldown(tmp_path):
    engine, store, mailer, settings = _engine(tmp_path, alert_email="ops@example.com")
    engine.create(name="run failed", metric="run_failed", comparator="gt", threshold=0,
                  channel="email", cooldown_seconds=3600)
    orch = make_orchestrator(tmp_path, budget_tokens=1, alert_email="ops@example.com")
    orch.mailer = mailer
    orch.alerts = engine
    run = await orch.run_to_completion("Build a game", auto_approve=True)
    assert run.status.value == "failed"
    assert mailer.outbox, "the alert should have been mailed"
    assert "alert:" in mailer.outbox[-1].subject
    assert store.list_audit(action="alert.fired")

    # second evaluation is inside the cooldown window
    assert await engine.evaluate(run) == []
    store.close()


async def test_token_threshold_and_quota_metrics(tmp_path):
    engine, store, mailer, settings = _engine(tmp_path, alert_email="ops@example.com")
    engine.create(name="tokens", metric="run_tokens", comparator="gte", threshold=1,
                  channel="email", cooldown_seconds=0)
    orch = make_orchestrator(tmp_path)
    orch.alerts = engine
    run = await orch.run_to_completion("Write a test plan", auto_approve=True)
    assert run.tokens > 0
    fired = await engine.evaluate(run)
    assert fired and fired[0]["delivered"] is True
    assert engine.measure("quota_used_pct", run) == 0.0
    store.close()


async def test_webhook_channel_without_a_url_is_reported(tmp_path):
    engine, store, mailer, settings = _engine(tmp_path)
    engine.create(name="hook", metric="run_tokens", comparator="gt", threshold=-1,
                  channel="webhook", cooldown_seconds=0)
    fired = await engine.evaluate(None)
    assert fired and fired[0]["delivered"] is False
    assert "webhook" in fired[0]["error"]
    store.close()


def test_alert_endpoints(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True,
                                             scheduler_enabled=False))) as client:
        body = client.post("/api/auth/register",
                           json={"email": "owner@example.com", "password": PASSWORD}).json()
        headers = {"Authorization": f"Bearer {body['token']}"}
        meta = client.get("/api/alert-rules", headers=headers).json()
        assert "run_tokens" in meta["metrics"] and "gt" in meta["comparators"]

        created = client.post("/api/alert-rules", headers=headers, json={
            "name": "spend", "metric": "run_cost_usd", "comparator": "gt", "threshold": 1.0,
            "channel": "email",
        })
        assert created.status_code == 201
        rule_id = created.json()["id"]
        assert client.post("/api/alert-rules", headers=headers,
                           json={"name": "bad", "metric": "nope", "comparator": "gt",
                                 "threshold": 1}).status_code == 400
        evaluated = client.post("/api/alert-rules/evaluate", headers=headers).json()
        assert "fired" in evaluated
        assert client.delete(f"/api/alert-rules/{rule_id}", headers=headers).json()["deleted"] == 1


# ---------------------------------------------------------- plugin registry
INDEX = {
    "plugins": [
        {"name": "acme-pack", "version": "1.0.0", "url": "https://cdn.example.com/acme-1.zip",
         "sha256": "", "description": "first", "permissions": ["net.read"]},
        {"name": "acme-pack", "version": "1.2.0", "url": "https://cdn.example.com/acme-1.2.zip",
         "sha256": "", "description": "newer", "permissions": ["net.read"]},
        {"name": "insecure", "version": "1.0.0", "url": "http://plain.example.com/x.zip"},
    ]
}


def _plugin_zip(manifest: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
    return buffer.getvalue()


MANIFEST = {"name": "acme-pack", "version": "1.2.0", "permissions": ["net.read"],
            "skills": [{"name": "acme_skill", "department": "research", "description": "x"}]}


def test_parse_index_filters_insecure_entries():
    entries = parse_index(INDEX)
    assert [e["name"] for e in entries] == ["acme-pack", "acme-pack"]
    with pytest.raises(PluginError):
        parse_index({"nope": []})


def test_find_picks_the_newest_version():
    entries = parse_index(INDEX)
    assert find(entries, "acme-pack")["version"] == "1.2.0"
    assert find(entries, "acme-pack", "1.0.0")["version"] == "1.0.0"
    with pytest.raises(PluginError):
        find(entries, "ghost")


async def test_fetch_index_requires_https():
    with pytest.raises(PluginError):
        await fetch_index("http://registry.example.com/index.json")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=INDEX))
    entries = await fetch_index("https://registry.example.com/index.json", transport=transport)
    assert len(entries) == 2


async def test_install_from_registry_verifies_checksum(tmp_path):
    import hashlib

    payload = _plugin_zip(MANIFEST)
    digest = hashlib.sha256(payload).hexdigest()
    index = {"plugins": [{"name": "acme-pack", "version": "1.2.0",
                          "url": "https://cdn.example.com/acme-1.2.zip", "sha256": digest}]}

    def handler(request: httpx.Request) -> httpx.Response:
        if "index.json" in str(request.url):
            return httpx.Response(200, json=index)
        return httpx.Response(200, content=payload)

    store = RunStore(tmp_path / "db.sqlite")
    settings = make_settings(tmp_path)
    record = await install_from_registry(
        store, tmp_path, "https://registry.example.com/index.json", "acme-pack", settings,
        None, "2026-01-01T00:00:00+00:00", transport=httpx.MockTransport(handler),
    )
    assert record["name"] == "acme-pack" and record["checksum_verified"] is True
    assert store.list_plugins()[0]["version"] == "1.2.0"
    store.close()


async def test_install_from_registry_rejects_a_bad_checksum(tmp_path):
    payload = _plugin_zip(MANIFEST)
    index = {"plugins": [{"name": "acme-pack", "version": "1.2.0",
                          "url": "https://cdn.example.com/acme-1.2.zip",
                          "sha256": "0" * 64}]}

    def handler(request: httpx.Request) -> httpx.Response:
        if "index.json" in str(request.url):
            return httpx.Response(200, json=index)
        return httpx.Response(200, content=payload)

    store = RunStore(tmp_path / "db.sqlite")
    with pytest.raises(PluginError) as exc:
        await install_from_registry(store, tmp_path, "https://registry.example.com/index.json",
                                    "acme-pack", make_settings(tmp_path), None, "t",
                                    transport=httpx.MockTransport(handler))
    assert "checksum" in str(exc.value)
    store.close()


# ---------------------------------------------------------- LLM moderation
class _Classifier:
    name = "test-model"
    model = "test-model"

    def __init__(self, verdict: dict) -> None:
        self.verdict = verdict
        self.calls = 0

    async def complete(self, system, prompt, json_mode=False):
        self.calls += 1
        from omniforge.core.llm import LLMResponse

        return LLMResponse(text=json.dumps(self.verdict), model="test-model")


async def test_llm_moderator_blocks_and_allows():
    moderator = LlmModerator(_Classifier({"categories": ["malware"], "block": True}),
                             enabled=True)
    verdict = await moderator.check("write a keylogger")
    assert verdict["checked"] and verdict["block"] is True
    assert "malware" in verdict["categories"]

    clean = LlmModerator(_Classifier({"categories": ["none"], "block": False}), enabled=True)
    assert (await clean.check("write a test plan"))["block"] is False


async def test_llm_moderator_fails_open_and_skips_mock():
    class Broken:
        name = "broken"
        model = "x"

        async def complete(self, *a, **k):
            raise RuntimeError("provider down")

    moderator = LlmModerator(Broken(), enabled=True)
    verdict = await moderator.check("anything")
    assert verdict["checked"] is False and verdict["block"] is False

    mock = LlmModerator(MockLLM(), enabled=True)
    assert mock.enabled is False
    assert (await mock.check("anything"))["checked"] is False


async def test_classifier_blocks_a_goal_end_to_end(tmp_path):
    orch = make_orchestrator(tmp_path, moderation_llm=True)
    orch.llm_moderator = LlmModerator(
        _Classifier({"categories": ["illegal"], "block": True}), enabled=True
    )
    with pytest.raises(ValueError) as exc:
        await orch.submit("do something illegal", auto_approve=True)
    assert "classifier" in str(exc.value)
    assert orch.metrics.counters.get("runs.submitted", 0) == 0


# ----------------------------------------------------- editor and sandbox
@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, allow_code_exec=True))) as c:
        yield c


def test_file_editor_roundtrip(client):
    project = client.post("/api/projects", json={"name": "Edits"}).json()
    upload = client.post(f"/api/projects/{project['id']}/files",
                         files={"file": ("notes.txt", io.BytesIO(b"v1"), "text/plain")}).json()
    saved = client.patch(f"/api/files/{upload['id']}", json={"content": "version two\n"})
    assert saved.status_code == 200 and saved.json()["bytes"] == 12
    assert client.get(f"/api/files/{upload['id']}/download").content == b"version two\n"
    assert client.patch("/api/files/file_missing", json={"content": "x"}).status_code == 404


def test_sandbox_status_and_run(client):
    status = client.get("/api/sandbox").json()
    assert status["enabled"] is True and status["available"] is True
    out = client.post("/api/sandbox/run", json={"code": "print(6*7)"}).json()
    assert out["ok"] is True and out["stdout"].strip() == "42"
    bad = client.post("/api/sandbox/run", json={"code": "raise SystemExit(2)"}).json()
    assert bad["ok"] is False and bad["exit_code"] == 2


def test_sandbox_disabled_by_default(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        assert c.get("/api/sandbox").json()["enabled"] is False
        assert c.post("/api/sandbox/run", json={"code": "print(1)"}).status_code == 409


def test_dashboard_has_workspace_and_builder(client):
    page = client.get("/").text
    assert 'id="v-workspace"' in page and 'id="v-builder"' in page
    assert "loadWorkspace" in page and "renderBuilder" in page
