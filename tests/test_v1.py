"""Streaming, task cancel, bulk import, settings, team routes, marketplace, speech."""
from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile

import httpx
import pytest
from conftest import make_orchestrator, make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core.alerts import AlertEngine
from omniforge.core.llm import LLMResponse, OpenAICompatibleLLM
from omniforge.core.media import MediaError, SpeechProvider
from omniforge.core.store import RunStore

PASSWORD = "hunter2hunter2"


# ---------------------------------------------------------------- streaming
def _sse_transport(chunks: list[str]) -> httpx.MockTransport:
    body = "".join(f'data: {json.dumps({"choices": [{"delta": {"content": c}}]})}\n\n'
                   for c in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=body.encode(),
                              headers={"content-type": "text/event-stream"})

    return httpx.MockTransport(handler)


async def test_openai_stream_emits_deltas():
    seen: list[str] = []

    async def on_token(delta: str) -> None:
        seen.append(delta)

    llm = OpenAICompatibleLLM("https://api.test/v1", "k", "gpt-4o-mini",
                              transport=_sse_transport(["Hello ", "world", "!"]))
    response = await llm.stream("system", "prompt", on_token)
    assert response.text == "Hello world!"
    assert seen == ["Hello ", "world", "!"]
    assert response.completion_tokens > 0


async def test_default_stream_falls_back_to_complete():
    class Once:
        name = "once"
        model = "once"

        async def complete(self, system, prompt, json_mode=False):
            return LLMResponse(text="whole answer", model="once")

    from omniforge.core.llm import BaseLLM

    class Adapter(BaseLLM):
        name = "adapter"
        model = "adapter"

        async def complete(self, system, prompt, *, json_mode=False):
            return LLMResponse(text="whole answer", model="adapter")

    seen: list[str] = []

    async def on_token(delta: str) -> None:
        seen.append(delta)

    response = await Adapter().stream("s", "p", on_token)
    assert response.text == "whole answer" and seen == ["whole answer"]


async def test_agent_streams_token_events(tmp_path):
    class Streamer:
        name = "streamer"
        model = "streamer"

        async def plan(self, goal, catalog):
            from omniforge.core.llm import heuristic_plan

            return heuristic_plan(goal, catalog)

        async def complete(self, system, prompt, json_mode=False):
            return LLMResponse(text="# Done\n\n- a\n- b\n- c", model="streamer")

        async def stream(self, system, prompt, on_token, *, json_mode=False):
            for piece in ("# Done\n\n", "- a\n", "- b\n", "- c"):
                result = on_token(piece)
                if hasattr(result, "__await__"):
                    await result
            return LLMResponse(text="# Done\n\n- a\n- b\n- c", model="streamer")

    orch = make_orchestrator(tmp_path, llm=Streamer(), prefer_generators=False)
    run = await orch.run_to_completion("Build a mobile game", auto_approve=True)
    tokens = [e for e in orch.bus.history(run_id=run.id) if e["type"] == "agent.token"]
    assert tokens, "no agent.token events were emitted"
    joined = "".join(e["payload"]["delta"] for e in tokens)
    assert "Done" in joined


# ------------------------------------------------------------------- cancel
async def test_cancel_task_skips_dependents(tmp_path):
    orch = make_orchestrator(tmp_path, require_approval=True)
    run = await orch.submit("Build a mobile game", auto_approve=False)
    first = None
    for _ in range(400):
        body = orch.store.get_run(run.id)
        tasks = (body.get("plan") or {}).get("tasks", [])
        waiting = [t for t in tasks if t["status"] == "awaiting_approval"]
        if waiting:
            first = waiting[0]
            break
        await asyncio.sleep(0.01)
    assert first is not None
    result = await orch.cancel_task(run.id, first["id"])
    assert result["changed"] is True and result["dependents_skipped"] >= 1
    statuses = [t["status"] for t in orch.store.get_run(run.id)["plan"]["tasks"]]
    assert "cancelled" in statuses and "skipped" in statuses

    # cancelling twice is a no-op
    assert (await orch.cancel_task(run.id, first["id"]))["changed"] is False
    with pytest.raises(KeyError):
        await orch.cancel_task(run.id, "task_missing")


def test_cancel_endpoint(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, require_approval=True))) as client:
        run = client.post("/api/runs", json={"goal": "Build a mobile game"}).json()
        task_id = None
        for _ in range(200):
            body = client.get(f"/api/runs/{run['id']}").json()
            waiting = [t for t in body["plan"]["tasks"] if t["status"] == "awaiting_approval"]
            if waiting:
                task_id = waiting[0]["id"]
                break
            time.sleep(0.01)
        assert task_id
        out = client.post(f"/api/runs/{run['id']}/tasks/{task_id}/cancel").json()
        assert out["status"] == "cancelled"
        assert client.post(f"/api/runs/{run['id']}/tasks/nope/cancel").status_code == 404


# ----------------------------------------------------------- bulk knowledge
def test_bulk_knowledge_import(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        payload = {"documents": [
            {"title": "Postgres tuning", "text": "shared_buffers and work_mem notes"},
            {"title": "Empty", "text": "   "},
            {"title": "Mobile build", "text": "gradle bundleRelease steps"},
        ]}
        out = client.post("/api/knowledge/import", json=payload).json()
        assert out == {"added": 2, "skipped": 1, "total": 3}
        hits = client.get("/api/knowledge/search", params={"q": "shared_buffers"}).json()["hits"]
        assert hits and hits[0]["title"] == "Postgres tuning"
        assert client.post("/api/knowledge/import", json={"documents": []}).status_code == 422


# ----------------------------------------------------------------- settings
def test_runtime_settings_apply_and_validate(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        before = client.get("/api/settings").json()
        assert "llm_model" in before["tunable"]
        applied = client.patch("/api/settings",
                               json={"values": {"debate_candidates": 2,
                                                "prefer_generators": False}}).json()
        assert applied["applied"]["debate_candidates"] == 2
        after = client.get("/api/settings").json()
        assert after["values"]["debate_candidates"] == 2
        assert after["values"]["prefer_generators"] is False
        assert "debate_candidates" in after["overrides"]
        # the orchestrator sees the change immediately
        assert client.app.state.orchestrator.settings.debate_candidates == 2
        assert client.patch("/api/settings", json={"values": {"nope": 1}}).status_code == 400
        assert client.delete("/api/settings/debate_candidates").json()["removed"] == 1


# ------------------------------------------------------------ team routing
def test_team_route_overrides_the_rule_target(tmp_path):
    settings = make_settings(tmp_path, alert_email="default@example.com")
    store = RunStore(settings.db_path)
    from omniforge.core.mailer import ConsoleMailer

    mailer = ConsoleMailer()
    engine = AlertEngine(store, settings, mailer=mailer)
    store.set_alert_route("ops", "email", "ops@example.com", "2026-01-01T00:00:00+00:00")
    rule = engine.create(name="tokens", metric="run_tokens", comparator="gt", threshold=0,
                         channel="email", team="ops", cooldown_seconds=0)
    assert engine.resolve_target(rule) == ("email", "ops@example.com")
    other = engine.create(name="plain", metric="run_tokens", comparator="gt", threshold=0,
                          channel="email", cooldown_seconds=0)
    assert engine.resolve_target(other) == ("email", "")
    store.close()


def test_alert_route_endpoints(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        created = client.post("/api/alert-routes", json={
            "team": "ops", "channel": "email", "target": "ops@example.com"}).json()
        assert created["team"] == "ops"
        assert client.get("/api/alert-routes").json()["routes"][0]["target"] == "ops@example.com"
        assert client.post("/api/alert-routes", json={
            "team": "team-x", "channel": "sms", "target": "123"}).status_code == 400
        assert client.delete("/api/alert-routes/ops").json()["removed"] == 1


# -------------------------------------------------------------- marketplace
def test_plugin_index_and_export(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, secret_key="k"))) as client:
        manifest = {"name": "demo-pack", "version": "1.0.0", "permissions": ["net.read"],
                    "skills": [{"name": "demo_skill", "department": "research",
                                "description": "demo"}]}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest))
        installed = client.post("/api/plugins/install",
                                files={"file": ("p.zip", buffer.getvalue(), "application/zip")})
        assert installed.status_code == 201, installed.text

        index = client.get("/api/plugins/index", params={"base_url": "https://example.com"}).json()
        assert index["count"] == 1
        entry = index["plugins"][0]
        assert entry["name"] == "demo-pack" and len(entry["sha256"]) == 64

        export = client.get(f"/api/plugins/{installed.json()['id']}/export")
        assert export.status_code == 200 and export.content[:2] == b"PK"
        with zipfile.ZipFile(io.BytesIO(export.content)) as bundle:
            assert json.loads(bundle.read("manifest.json"))["name"] == "demo-pack"
        assert client.get("/api/plugins/plugin_missing/export").status_code == 404


# ------------------------------------------------------------------- speech
async def test_speech_provider_generates_audio():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["voice"] == "alloy"
        return httpx.Response(200, content=b"ID3-fake-mp3")

    provider = SpeechProvider("k", transport=httpx.MockTransport(handler))
    assert await provider.speak("hello") == b"ID3-fake-mp3"
    with pytest.raises(MediaError):
        await SpeechProvider("").speak("hello")
    with pytest.raises(MediaError):
        await SpeechProvider("k", transport=httpx.MockTransport(lambda r: httpx.Response(500))).speak("x")


def test_speech_endpoint_needs_a_key(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.post("/api/media/speech", json={"text": "hi"}).status_code == 409
        assert client.get("/api/media").json()["speech_generation"] is False


# --------------------------------------------------------------- dashboard
def test_dashboard_has_settings_stream_and_graph(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        page = client.get("/").text
        assert 'id="v-settings"' in page
        assert 'id="live"' in page and "agent.token" in page
        assert "renderGraph" in page and 'id="b-graph"' in page
