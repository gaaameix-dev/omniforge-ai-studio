"""Debate, media, engine scaffolds, provider budgets and queue-backed workers."""
from __future__ import annotations

import base64

import httpx
import pytest
from conftest import FixedPlanner, make_orchestrator, make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core.billing import Billing
from omniforge.core.engines import ENGINES, scaffold, summarise
from omniforge.core.media import ImageProvider, MediaError
from omniforge.core.media import status as media_status
from omniforge.core.store import RunStore
from omniforge.core.worker import Worker

PASSWORD = "hunter2hunter2"


# ------------------------------------------------------------------- debate
async def test_debate_runs_candidates_and_keeps_the_best(tmp_path):
    state = {"calls": 0}

    def handler(ctx):
        state["calls"] += 1
        # the second candidate is richer, so it should win
        if state["calls"] % 2 == 0:
            return "# Deliverable\n\n- point one with detail\n- point two with detail\n- three"
        return "# Short\n\n- one"

    from omniforge.agents.roles import build_skill_registry
    from omniforge.skills.base import Skill

    skills = build_skill_registry()
    skills.register("debate_demo", Skill(name="debate_demo", department="research",
                                         description="demo", handler=handler))
    orch = make_orchestrator(
        tmp_path, skills=skills, debate_candidates=2, max_retries=1,
        llm=FixedPlanner([{"key": "a", "title": "Debated", "department": "research",
                           "skill": "debate_demo", "depends_on": []}]),
    )
    run = await orch.run_to_completion("pick the best answer", auto_approve=True)
    task = run.plan.tasks[0]
    assert task.status.value == "done"
    assert state["calls"] >= 2
    assert "point two" in (task.result or "")
    events = [e for e in orch.bus.history(run_id=run.id) if e["type"] == "task.debated"]
    assert events and events[0]["payload"]["candidates"] == 2
    assert len(events[0]["payload"]["scores"]) == 2


def test_debate_is_off_by_default(tmp_path):
    assert make_settings(tmp_path).debate_candidates == 1


# -------------------------------------------------------------------- media
def _image_transport(b64: bool = True, status_code: int = 200) -> httpx.MockTransport:
    payload = base64.b64encode(b"\x89PNG-fake").decode()

    def handler(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code, text="nope")
        if b64:
            return httpx.Response(200, json={"data": [{"b64_json": payload}]})
        return httpx.Response(200, json={"data": [{"url": "https://cdn.test/i.png"}]})

    return httpx.MockTransport(handler)


async def test_image_provider_decodes_base64():
    provider = ImageProvider("k", transport=_image_transport())
    data = await provider.generate("a neon car", size="512x512")
    assert data.startswith(b"\x89PNG")


async def test_image_provider_follows_a_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=b"image-bytes")
        return httpx.Response(200, json={"data": [{"url": "https://cdn.test/i.png"}]})

    provider = ImageProvider("k", transport=httpx.MockTransport(handler))
    assert await provider.generate("x") == b"image-bytes"


async def test_image_provider_errors():
    with pytest.raises(MediaError):
        await ImageProvider("").generate("x")
    with pytest.raises(MediaError):
        await ImageProvider("k", transport=_image_transport(status_code=500)).generate("x")
    with pytest.raises(MediaError):
        await ImageProvider("k", transport=_image_transport()).generate("   ")
    empty = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []}))
    with pytest.raises(MediaError):
        await ImageProvider("k", transport=empty).generate("x")


def test_media_status_is_honest(tmp_path):
    assert media_status(make_settings(tmp_path))["image_generation"] is False
    assert media_status(make_settings(tmp_path))["model_3d_generation"] is False


def test_image_endpoint_needs_a_key(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/api/media").json()["image_generation"] is False
        assert client.post("/api/media/image", json={"prompt": "a car"}).status_code == 409


# ------------------------------------------------------------------ engines
@pytest.mark.parametrize("engine", ENGINES)
def test_every_engine_scaffold_is_openable(engine):
    files = scaffold(engine, name="Neon Drift", genre="racing", notes="prototype")
    summary = summarise(files)
    assert summary["count"] >= 3
    assert all(text.strip() for text in files.values())
    joined = " ".join(files.values())
    assert "Neon" in joined or "Neon_Drift" in joined


def test_engine_specific_files():
    godot = scaffold("godot")
    assert "project.godot" in godot and "scenes/main.tscn" in godot
    unity = scaffold("unity")
    assert any(p.endswith(".cs") for p in unity)
    unreal = scaffold("unreal", name="My Game")
    assert any(p.endswith(".uproject") for p in unreal)


def test_unknown_engine_is_rejected():
    with pytest.raises(ValueError):
        scaffold("cryengine")


def test_engine_endpoint(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/api/engines").json()["engines"] == list(ENGINES)
        out = client.post("/api/engines/scaffold",
                          json={"engine": "unity", "name": "Racer"}).json()
        assert out["count"] >= 3 and out["files"]
        assert client.post("/api/engines/scaffold", json={"engine": "nope"}).status_code == 400


# ---------------------------------------------------------- provider budgets
def test_provider_mapping_and_usage(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    billing = Billing(make_settings(tmp_path, provider_budgets={"openai": 1.0}), store)
    assert billing.provider_of("gpt-4o-mini") == "openai"
    assert billing.provider_of("claude-3-5-sonnet") == "anthropic"
    assert billing.provider_of("gemini-1.5-pro") == "google"
    billing.record(user_id="u", run_id="r", model="gpt-4o-mini", input_tokens=1_000_000,
                   output_tokens=0, ts="2026-11-01T00:00:00+00:00")
    rows = {row["provider"]: row for row in billing.provider_usage()}
    assert rows["openai"]["cost_usd"] == pytest.approx(0.15, abs=0.01)
    assert rows["openai"]["budget_usd"] == 1.0
    assert rows["openai"]["exhausted"] is False
    billing.check_provider_budget("gpt-4o-mini")
    store.close()


def test_provider_budget_blocks_when_exhausted(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    billing = Billing(make_settings(tmp_path, provider_budgets={"openai": 0.001}), store)
    billing.record(user_id="u", run_id="r", model="gpt-4o-mini", input_tokens=1_000_000,
                   output_tokens=0, ts="2026-11-01T00:00:00+00:00")
    with pytest.raises(PermissionError):
        billing.check_provider_budget("gpt-4o-mini")
    billing.check_provider_budget("claude-3-5-sonnet")     # no budget for anthropic
    store.close()


def test_provider_endpoints(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        payload = client.get("/api/billing/providers").json()
        assert "providers" in payload and "budgets" in payload


# ---------------------------------------------------------------- workers
async def test_queue_mode_enqueues_instead_of_running(tmp_path):
    orch = make_orchestrator(tmp_path, worker_mode=True)
    run = await orch.submit("Build a mobile game", auto_approve=True)
    assert run.status.value == "queued"
    assert orch.store.queued_count() == 1
    await asyncio_sleep(0.1)
    assert orch.store.get_run(run.id)["status"] == "queued"


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def test_worker_processes_a_queued_run(tmp_path):
    api = make_orchestrator(tmp_path, worker_mode=True)
    run = await api.submit("Write a test plan", auto_approve=True)
    worker = Worker(make_orchestrator(tmp_path, worker_mode=True), worker_id="w-test")
    result = await worker.run_once()
    assert result["run_id"] == run.id and result["status"] == "done"
    assert api.store.get_run(run.id)["status"] == "done"
    assert api.store.queued_count() == 0
    assert await worker.run_once() is None


async def test_two_workers_never_double_claim(tmp_path):
    api = make_orchestrator(tmp_path, worker_mode=True)
    runs = [await api.submit(f"Build a mobile game {i}", auto_approve=True) for i in range(3)]
    w1 = Worker(make_orchestrator(tmp_path, worker_mode=True), worker_id="w1")
    w2 = Worker(make_orchestrator(tmp_path, worker_mode=True), worker_id="w2")
    processed = []
    for _ in range(4):
        for worker in (w1, w2):
            result = await worker.run_once()
            if result:
                processed.append(result["run_id"])
    assert sorted(processed) == sorted(r.id for r in runs)
    assert len(set(processed)) == 3
    assert w1.processed + w2.processed == 3


def test_expired_lease_is_requeued(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    from omniforge.core.tasks import Plan, Run, RunStatus

    run = Run(goal="g", plan=Plan(goal="g"), auto_approve=True)
    run.status = RunStatus.QUEUED
    store.save_run(run)
    claimed = store.claim_next_run("w1", 60, "2026-01-01T00:00:00+00:00",
                                   "2026-01-01T00:01:00+00:00")
    assert claimed and claimed["id"] == run.id
    # lease expired: the run goes back to the queue
    assert store.requeue_expired("2026-01-01T00:05:00+00:00") == 1
    assert store.queued_count() == 1
    store.close()


def test_workers_endpoint(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, worker_mode=True))) as client:
        info = client.get("/api/workers").json()
        assert info["worker_mode"] is True
        created = client.post("/api/runs", json={"goal": "Build a mobile game",
                                                 "auto_approve": True}).json()
        assert created["status"] == "queued"
        assert client.get("/api/workers").json()["queued"] == 1
