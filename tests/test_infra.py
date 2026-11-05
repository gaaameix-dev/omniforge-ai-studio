"""Media job API, engine builds, broker, leader lease, tenancy, tracing, OIDC."""
from __future__ import annotations

import json
import time

import httpx
import pytest
from conftest import make_orchestrator, make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core.broker import DbBroker, RedisBroker, build_broker
from omniforge.core.engine_build import available, build, resolve_command
from omniforge.core.leader import LeaderLease
from omniforge.core.media_jobs import JobMediaError, JobMediaProvider, validate_gltf
from omniforge.core.oidc import OIDCClient, OIDCError
from omniforge.core.store import RunStore
from omniforge.core.tracing import Tracer

PASSWORD = "hunter2hunter2"


# ------------------------------------------------------- job media provider
def _job_transport(states: list[dict], asset: bytes = b"asset-bytes") -> httpx.MockTransport:
    calls = {"poll": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"id": "job_1", "status": "pending"})
        if request.url.path.endswith("/jobs/job_1"):
            index = min(calls["poll"], len(states) - 1)
            calls["poll"] += 1
            return httpx.Response(200, json=states[index])
        if request.url.path.endswith("/asset"):
            return httpx.Response(200, content=asset)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_media_job_polls_until_done():
    transport = _job_transport([{"status": "pending"},
                                {"status": "done", "asset_url": "https://cdn.test/asset"}])
    provider = JobMediaProvider("https://jobs.test", "k", transport=transport, poll_interval=0)
    result = await provider.generate("video", "a neon city at night")
    assert result["bytes"] == b"asset-bytes" and result["job_id"] == "job_1"


async def test_media_job_rejects_bad_input():
    provider = JobMediaProvider("https://jobs.test", transport=_job_transport([{"status": "done"}]))
    with pytest.raises(JobMediaError):
        await provider.generate("podcast", "x")
    with pytest.raises(JobMediaError):
        await provider.generate("video", "   ")
    with pytest.raises(JobMediaError):
        await JobMediaProvider("").generate("video", "x")


async def test_media_job_reports_failure():
    transport = _job_transport([{"status": "failed", "error": "gpu quota"}])
    provider = JobMediaProvider("https://jobs.test", transport=transport, poll_interval=0)
    with pytest.raises(JobMediaError) as exc:
        await provider.generate("video", "x")
    assert "gpu quota" in str(exc.value)


async def test_3d_job_validates_gltf():
    assert validate_gltf(b"glTF\x02\x00\x00\x00rest")["valid"] is True
    assert validate_gltf(json.dumps({"asset": {"version": "2.0"}}).encode())["valid"] is True
    assert validate_gltf(b"not a model")["valid"] is False
    transport = _job_transport([{"status": "done", "asset_url": "https://cdn.test/asset"}],
                               asset=b"glTF\x02\x00\x00\x00")
    provider = JobMediaProvider("https://jobs.test", transport=transport, poll_interval=0)
    result = await provider.generate("model3d", "a low-poly crate")
    assert result["validation"]["valid"] is True


def test_media_status_reflects_the_job_provider(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/api/media").json()["video_generation"] is False
    with TestClient(create_app(make_settings(tmp_path, media_job_url="https://jobs.test"))) as client:
        payload = client.get("/api/media").json()
        assert payload["video_generation"] and payload["music_generation"]
        assert payload["model_3d_generation"]


# ----------------------------------------------------------- engine builds
def test_engine_commands_resolve(tmp_path):
    command = resolve_command("godot", tmp_path, make_settings(tmp_path))
    assert command[0] == "godot" and str(tmp_path) in command
    with pytest.raises(Exception):
        resolve_command("cryengine", tmp_path, make_settings(tmp_path))


def test_engine_availability_is_honest(tmp_path):
    report = available("godot", make_settings(tmp_path))
    assert report["configured"] is True
    assert "found" in report


async def test_engine_build_reports_a_missing_binary(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    result = await build("godot", project, settings=make_settings(tmp_path))
    assert result["ok"] is False
    assert "not installed" in result["error"]


async def test_engine_build_runs_a_configured_command(tmp_path):
    script = tmp_path / "fake_engine.sh"
    script.write_text("#!/bin/sh\necho building\nexit 0\n")
    script.chmod(0o755)
    project = tmp_path / "proj"
    project.mkdir()
    settings = make_settings(tmp_path, engine_commands={"godot": f"{script} {{project}}"})
    result = await build("godot", project, settings=settings)
    assert result["ok"] is True
    assert "building" in result["log_tail"]


# ---------------------------------------------------------------- broker
def test_db_broker_claims_and_releases(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    from omniforge.core.tasks import Plan, Run, RunStatus

    run = Run(goal="g", plan=Plan(goal="g"), auto_approve=True)
    run.status = RunStatus.QUEUED
    store.save_run(run)
    broker = DbBroker(store)
    claimed = broker.claim("w1", 60)
    assert claimed and claimed["id"] == run.id
    broker.release(run.id)
    assert store.get_run(run.id)["lease_owner"] is None
    store.close()


def test_redis_broker_roundtrip():
    fakeredis = pytest.importorskip("fakeredis")
    broker = RedisBroker(fakeredis.FakeRedis())
    broker.enqueue("run_a")
    broker.enqueue("run_b")
    assert broker.stats()["queued"] == 2
    first = broker.claim("w1")
    assert first["id"] == "run_a"
    broker.release(first["id"])
    assert broker.claim("w2")["id"] == "run_b"


def test_redis_broker_requeues_expired_leases():
    fakeredis = pytest.importorskip("fakeredis")
    broker = RedisBroker(fakeredis.FakeRedis())
    broker.enqueue("run_a")
    broker.claim("w1", lease_seconds=-1)          # already expired
    assert broker.requeue_expired() == ["run_a"]
    assert broker.stats()["queued"] == 1


def test_broker_factory(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    assert build_broker(make_settings(tmp_path), store).name == "database"
    store.close()


# ----------------------------------------------------------- leader lease
def test_only_one_leader_at_a_time(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    first = LeaderLease(store, owner="a", ttl_seconds=60)
    second = LeaderLease(store, owner="b", ttl_seconds=60)
    assert first.acquire() is True
    assert second.acquire() is False
    assert first.holder() == "a"
    assert first.release() is True
    assert second.acquire() is True
    store.close()


def test_expired_lease_can_be_taken(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    stale = LeaderLease(store, owner="a", ttl_seconds=-5)
    stale.acquire()
    fresh = LeaderLease(store, owner="b", ttl_seconds=60)
    assert fresh.acquire() is True
    assert fresh.holder() == "b"
    store.close()


# ---------------------------------------------------------------- tracing
async def test_spans_are_recorded_per_task(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.run_to_completion("Write a test plan", auto_approve=True)
    summary = orch.tracer.summary()
    assert summary["spans"] >= len(run.plan.tasks)
    assert "task" in summary["by_name"]
    spans = orch.tracer.recent(limit=5, trace_id=orch.tracer.spans[0].trace_id)
    assert spans and spans[0]["duration_ms"] >= 0


def test_tracer_records_errors():
    tracer = Tracer()
    with pytest.raises(RuntimeError):
        with tracer.span("boom", run_id="r1"):
            raise RuntimeError("nope")
    recent = tracer.recent()
    assert recent[0]["status"] == "error" and "nope" in recent[0]["error"]


def test_traces_and_ready_endpoints(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        client.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True})
        for _ in range(200):
            body = client.get("/api/traces").json()
            if body["summary"]["spans"]:
                break
            time.sleep(0.02)
        assert body["summary"]["spans"] >= 1
        ready = client.get("/api/ready").json()
        assert ready["ready"] is True and ready["checks"]["database"] is True


# ---------------------------------------------------------------- tenancy
@pytest.fixture(scope="module")
def pg_url():
    pytest.importorskip("pgserver")
    pytest.importorskip("psycopg")
    import tempfile

    import pgserver

    return pgserver.get_server(tempfile.mkdtemp()).get_uri()


def test_tenant_schemas_are_isolated(pg_url):
    from omniforge.core.tenancy import create_tenant, drop_tenant, list_tenants, schema_name

    alpha = create_tenant(pg_url, "Alpha Co")
    beta = create_tenant(pg_url, "beta")
    assert alpha["schema"] == "tenant_alpha_co" and alpha["tables"] > 10
    assert beta["schema"] == "tenant_beta"
    assert {row["schema"] for row in list_tenants(pg_url)} >= {alpha["schema"], beta["schema"]}

    from omniforge.core.store_pg import PostgresStore

    store_alpha = PostgresStore(pg_url, schema=alpha["schema"])
    store_beta = PostgresStore(pg_url, schema=beta["schema"])
    store_alpha.create_project("alpha only", "2026-01-01T00:00:00+00:00")
    assert [p["name"] for p in store_alpha.list_projects()] == ["alpha only"]
    assert store_beta.list_projects() == []
    store_alpha.close()
    store_beta.close()
    assert drop_tenant(pg_url, "Alpha Co")["dropped"] is True
    with pytest.raises(Exception):
        schema_name("!!")


# ------------------------------------------------------------------- OIDC
def _oidc_transport(verified: bool = True) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "openid-configuration" in url:
            return httpx.Response(200, json={"authorization_endpoint": "https://idp.test/authorize",
                                             "token_endpoint": "https://idp.test/token",
                                             "userinfo_endpoint": "https://idp.test/userinfo"})
        if url.endswith("/token"):
            return httpx.Response(200, json={"access_token": "at"})
        if url.endswith("/userinfo"):
            return httpx.Response(200, json={"email": "oidc@example.com", "name": "OIDC User",
                                             "email_verified": verified, "sub": "abc"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_oidc_pkce_and_identify():
    client = OIDCClient("https://idp.test", "client", redirect_uri="http://localhost/cb",
                        transport=_oidc_transport())
    verifier, challenge = client.new_pkce()
    assert len(verifier) > 40 and len(challenge) > 20
    url = await client.authorization_url("state1", challenge)
    assert "code_challenge_method=S256" in url and "state=state1" in url
    identity = await client.identify("code", verifier)
    assert identity["email"] == "oidc@example.com"


async def test_oidc_rejects_unverified_email():
    client = OIDCClient("https://idp.test", "client", transport=_oidc_transport(verified=False))
    with pytest.raises(OIDCError):
        await client.identify("code", "verifier")


async def test_oidc_disabled_reports_clearly():
    with pytest.raises(OIDCError):
        await OIDCClient("", "").discover()


def test_oidc_login_flow(tmp_path):
    settings = make_settings(tmp_path, auth_enabled=True, oidc_issuer="https://idp.test",
                             oidc_client_id="client", oidc_redirect_uri="http://localhost/cb")
    app = create_app(settings)
    app.state.oidc_transport = _oidc_transport()
    with TestClient(app) as client:
        start = client.get("/api/auth/oidc/login").json()
        assert "authorize" in start["authorization_url"]
        done = client.get("/api/auth/oidc/callback",
                          params={"code": "c", "state": start["state"]})
        assert done.status_code == 200
        assert done.json()["user"]["email"] == "oidc@example.com"
        assert done.json()["user"]["role"] == "owner"
        replay = client.get("/api/auth/oidc/callback",
                            params={"code": "c", "state": start["state"]})
        assert replay.status_code == 400


def test_oidc_endpoints_need_configuration(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/api/auth/oidc/login").status_code == 409
