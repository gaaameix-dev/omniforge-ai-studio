"""Model adapters, gateway failover, billing, plugins and the Python SDK."""
from __future__ import annotations

import io
import json
import time
import zipfile

import httpx
import pytest
from conftest import make_orchestrator, make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.client import OmniForgeClient, OmniForgeError
from omniforge.core.billing import Billing
from omniforge.core.gateway import ModelGateway
from omniforge.core.llm import AnthropicLLM, GeminiLLM, MockLLM, build_llm
from omniforge.core.store import RunStore

PASSWORD = "hunter2hunter2"


# ------------------------------------------------------------------ adapters
def _anthropic_transport(seen: list) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "headers": dict(request.headers),
                     "body": json.loads(request.content)})
        return httpx.Response(200, json={
            "model": "claude-3-5-sonnet-latest",
            "content": [{"type": "text", "text": "hello from claude"}],
            "usage": {"input_tokens": 12, "output_tokens": 8},
        })
    return httpx.MockTransport(handler)


def _gemini_transport(seen: list) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "hello from gemini"}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
        })
    return httpx.MockTransport(handler)


async def test_anthropic_adapter_shapes_the_request():
    seen: list = []
    llm = AnthropicLLM("key-123", "claude-3-5-sonnet-latest", transport=_anthropic_transport(seen))
    response = await llm.complete("be terse", "say hi", json_mode=True)
    assert response.text == "hello from claude"
    assert response.prompt_tokens == 12 and response.completion_tokens == 8
    assert seen[0]["headers"]["x-api-key"] == "key-123"
    assert seen[0]["body"]["system"].startswith("be terse")
    assert seen[0]["body"]["messages"][0]["content"] == "say hi"


async def test_gemini_adapter_shapes_the_request():
    seen: list = []
    llm = GeminiLLM("g-key", "gemini-1.5-flash", transport=_gemini_transport(seen))
    response = await llm.complete("be terse", "say hi", json_mode=True)
    assert response.text == "hello from gemini"
    assert response.prompt_tokens == 5
    assert "key=g-key" in seen[0]["url"]
    assert seen[0]["body"]["systemInstruction"]["parts"][0]["text"] == "be terse"
    assert seen[0]["body"]["generationConfig"]["responseMimeType"] == "application/json"


async def test_gateway_fails_over_and_counts():
    class Broken:
        name = "broken"
        model = "x"

        async def complete(self, *a, **k):
            raise RuntimeError("provider down")

    good = AnthropicLLM("k", "claude", transport=_anthropic_transport([]))
    gateway = ModelGateway([Broken(), good])
    response = await gateway.complete("s", "p")
    assert response.text == "hello from claude"
    assert gateway.failures["broken"] == 1 and gateway.successes["anthropic"] == 1
    status = gateway.status()
    assert status["providers"] == ["broken", "anthropic"]


async def test_gateway_raises_when_every_provider_fails():
    class Broken:
        name = "broken"
        model = "x"

        async def complete(self, *a, **k):
            raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        await ModelGateway([Broken(), Broken()]).complete("s", "p")


def test_build_llm_selects_provider_and_fallbacks(tmp_path):
    mock = build_llm(make_settings(tmp_path))
    assert isinstance(mock, MockLLM)

    settings = make_settings(tmp_path, mock_llm=False, llm_provider="anthropic",
                             anthropic_api_key="a-key",
                             llm_fallback=("gemini:gemini-1.5-flash",),
                             gemini_api_key="g-key")
    gateway = build_llm(settings)
    assert isinstance(gateway, ModelGateway)
    assert gateway.provider_names == ["anthropic", "gemini"]

    only_primary = build_llm(make_settings(tmp_path, mock_llm=False, llm_api_key="sk-x"))
    assert only_primary.name == "openai-compatible"


# ------------------------------------------------------------------- billing
def test_price_table_and_prefix_matching(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    billing = Billing(make_settings(tmp_path, model_prices={"my-model": [1.0, 2.0]}), store)
    assert billing.price_for("gpt-4o-mini") == (0.15, 0.60)
    assert billing.price_for("gpt-4o-mini-2024-07-18") == (0.15, 0.60)
    assert billing.price_for("my-model-v2") == (1.0, 2.0)
    assert billing.price_for("unknown-model") is None
    assert billing.cost("unknown-model", 1000, 1000) == 0.0
    assert round(billing.cost("my-model", 1_000_000, 1_000_000), 2) == 3.0
    assert any(row["model"] == "my-model" for row in billing.pricing())
    store.close()


def test_usage_and_quota(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    billing = Billing(make_settings(tmp_path, user_monthly_tokens=1000), store)
    billing.record(user_id="u1", run_id="r1", model="gpt-4o-mini",
                   input_tokens=400, output_tokens=200, ts="2026-11-02T00:00:00+00:00")
    usage = billing.usage("u1")
    assert usage["tokens"] == 600 and usage["quota_remaining"] == 400
    assert usage["by_model"][0]["model"] == "gpt-4o-mini"
    assert billing.quota_remaining("u1") == 400
    billing.check_quota("u1")
    billing.record(user_id="u1", run_id="r2", model="gpt-4o-mini",
                   input_tokens=600, output_tokens=0, ts="2026-11-02T01:00:00+00:00")
    assert billing.quota_remaining("u1") == 0
    with pytest.raises(PermissionError):
        billing.check_quota("u1")
    store.close()


def test_estimate_is_labelled_an_estimate(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    estimate = Billing(make_settings(tmp_path), store).estimate("build a game", tasks=4)
    assert estimate["calls"] == 6 and estimate["estimated_cost_usd"] >= 0
    assert "estimate" in estimate["note"]
    store.close()


async def test_run_records_usage(tmp_path):
    orch = make_orchestrator(tmp_path)
    orch.billing = Billing(make_settings(tmp_path), orch.store)
    await orch.run_to_completion("Write a test plan", auto_approve=True)
    usage = orch.billing.usage(None, this_month=False)
    assert usage["entries"] == 1
    assert usage["by_model"][0]["model"]


# ------------------------------------------------------------------- plugins
def _plugin_zip(manifest: dict, *, extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("README.md", "# plugin")
        for name, payload in (extra or {}).items():
            bundle.writestr(name, payload)
    return buffer.getvalue()


MANIFEST = {
    "name": "acme-pack",
    "version": "1.0.0",
    "description": "Two skills and one agent",
    "permissions": ["net.read"],
    "skills": [
        {"name": "acme_brief", "department": "research", "description": "Writes a brief",
         "min_chars": 20},
        {"name": "acme_review", "department": "qa", "description": "Reviews a plan"},
    ],
    "agents": [
        {"name": "Acme Analyst", "department": "research", "description": "Plugin analyst",
         "skills": ["acme_brief", "acme_review"], "primary_skills": ["acme_brief"]},
    ],
}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True,
                                             scheduler_enabled=False))) as c:
        yield c


def _headers(client, email="owner@example.com"):
    body = client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD, "name": "Owner"}).json()
    return {"Authorization": f"Bearer {body['token']}"}


def test_plugin_install_enable_disable_uninstall(client):
    headers = _headers(client)
    install = client.post("/api/plugins/install",
                          files={"file": ("p.zip", io.BytesIO(_plugin_zip(MANIFEST)),
                                          "application/zip")}, headers=headers)
    assert install.status_code == 201, install.text
    record = install.json()
    assert record["trust"] == "unsigned" and record["version"] == "1.0.0"
    plugin_id = record["id"]

    enabled = client.post(f"/api/plugins/{plugin_id}/enable", headers=headers).json()
    assert "acme_brief" in enabled["skills"] and enabled["agents"]
    assert any(s["name"] == "acme_brief" for s in client.get("/api/skills", headers=headers).json())
    assert any(a["name"] == "Acme Analyst"
               for a in client.get("/api/agents", headers=headers).json())

    disabled = client.post(f"/api/plugins/{plugin_id}/disable", headers=headers).json()
    assert disabled["enabled"] is False
    assert not any(s["name"] == "acme_brief"
                   for s in client.get("/api/skills", headers=headers).json())

    assert client.delete(f"/api/plugins/{plugin_id}", headers=headers).json()["deleted"] == 1
    assert client.get("/api/plugins", headers=headers).json() == []


def test_signed_plugin_is_verified(tmp_path):
    from omniforge.core.plugins import sign

    secret = "deployment-secret"
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True,
                                             scheduler_enabled=False,
                                             secret_key=secret))) as c:
        headers = _headers(c)
        manifest = dict(MANIFEST, signature=sign(MANIFEST, secret))
        install = c.post("/api/plugins/install",
                         files={"file": ("p.zip", io.BytesIO(_plugin_zip(manifest)),
                                         "application/zip")}, headers=headers)
        assert install.status_code == 201
        assert install.json()["trust"] == "verified"

        # a tampered manifest no longer verifies
        tampered = dict(MANIFEST, version="9.9.9", signature=sign(MANIFEST, secret))
        resp = c.post("/api/plugins/install",
                      files={"file": ("p2.zip", io.BytesIO(_plugin_zip(tampered)),
                                      "application/zip")}, headers=headers)
        assert resp.status_code == 201 and resp.json()["trust"] == "unsigned"


def test_plugin_validation_rejects_bad_packages(client):
    headers = _headers(client)
    bad_permission = dict(MANIFEST, permissions=["code.exec"])
    resp = client.post("/api/plugins/install",
                       files={"file": ("p.zip", io.BytesIO(_plugin_zip(bad_permission)),
                                       "application/zip")}, headers=headers)
    assert resp.status_code == 400 and "allowlist" in resp.json()["detail"]

    unknown_skill = dict(MANIFEST, agents=[{"name": "A", "department": "research",
                                            "skills": ["nope"]}])
    resp = client.post("/api/plugins/install",
                       files={"file": ("p.zip", io.BytesIO(_plugin_zip(unknown_skill)),
                                       "application/zip")}, headers=headers)
    assert resp.status_code == 400 and "unknown skill" in resp.json()["detail"]

    no_manifest = io.BytesIO()
    with zipfile.ZipFile(no_manifest, "w") as bundle:
        bundle.writestr("x.txt", "hi")
    no_manifest.seek(0)
    resp = client.post("/api/plugins/install",
                       files={"file": ("p.zip", no_manifest, "application/zip")}, headers=headers)
    assert resp.status_code == 400 and "manifest" in resp.json()["detail"]


def test_unsigned_plugins_can_be_forbidden(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True,
                                             scheduler_enabled=False,
                                             plugin_allow_unsigned=False))) as c:
        headers = _headers(c)
        resp = c.post("/api/plugins/install",
                      files={"file": ("p.zip", io.BytesIO(_plugin_zip(MANIFEST)),
                                      "application/zip")}, headers=headers)
        assert resp.status_code == 400 and "unsigned" in resp.json()["detail"]


# ------------------------------------------------------------------ billing API
def test_billing_endpoints(client):
    headers = _headers(client)
    pricing = client.get("/api/billing/pricing", headers=headers).json()
    assert pricing["unit"] == "USD per 1M tokens" and pricing["models"]
    estimate = client.post("/api/billing/estimate", json={"goal": "build a game", "tasks": 3},
                           headers=headers).json()
    assert estimate["estimated_input_tokens"] > 0
    run = client.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True},
                      headers=headers).json()
    for _ in range(200):
        body = client.get(f"/api/runs/{run['id']}", headers=headers).json()
        if body["status"] in {"done", "failed"}:
            break
        time.sleep(0.02)
    usage = client.get("/api/billing/usage", headers=headers).json()
    assert usage["entries"] >= 1 and usage["tokens"] > 0
    quota = client.get("/api/billing/quota", headers=headers).json()
    assert quota["quota_tokens"] == 0 and quota["exhausted"] is False


def test_quota_blocks_new_runs(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True,
                                             scheduler_enabled=False,
                                             user_monthly_tokens=1))) as c:
        headers = _headers(c)
        first = c.post("/api/runs", json={"goal": "Write a test plan", "auto_approve": True},
                       headers=headers).json()
        for _ in range(200):
            body = c.get(f"/api/runs/{first['id']}", headers=headers).json()
            if body["status"] in {"done", "failed"}:
                break
            time.sleep(0.02)
        blocked = c.post("/api/runs", json={"goal": "Write another plan", "auto_approve": True},
                         headers=headers)
        assert blocked.status_code == 402


# ---------------------------------------------------------------------- SDK
async def test_sdk_round_trip(tmp_path):
    app = create_app(make_settings(tmp_path, scheduler_enabled=False))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with OmniForgeClient("http://test", transport=transport) as client:
            health = await client.health()
            assert health["status"] == "ok"
            framework = await client.framework()
            assert framework["counts"]["agents"] == 120
            assert len(await client.agents(department="qa")) == 12
            assert await client.skills(department="web")

            run = await client.run("Build a mobile puzzle game", auto_approve=True, timeout=60)
            assert run["status"] == "done"
            artifacts = await client.artifacts(run["id"])
            assert any(a["name"] == "summary.md" for a in artifacts)
            body = await client.download_artifact(run["id"], "summary.md")
            assert b"Handoff summary" in body

            workflows = await client.workflows()
            created = await client.workflow_from_run(run["id"], "sdk workflow")
            assert created["id"]
            replayed = await client.run_workflow(created["id"], {"x": "y"})
            assert (await client.wait(replayed["id"], timeout=60))["status"] == "done"
            assert workflows == []

            usage = await client.usage()
            assert usage["entries"] >= 1
            monitor = await client.monitor()
            assert monitor["agents"]["total"] == 120


async def test_sdk_surfaces_server_errors(tmp_path):
    app = create_app(make_settings(tmp_path, scheduler_enabled=False))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with OmniForgeClient("http://test", transport=transport) as client:
            with pytest.raises(OmniForgeError) as exc:
                await client.get_run("run_missing")
            assert exc.value.status == 404
            with pytest.raises(ValueError):
                await client.control("explode")
