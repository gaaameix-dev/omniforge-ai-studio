"""Backlog items closed in 1.1: rubric, cost cap, delegation, backups, import,
alert severity, plugin signing, debate judge, streamed summary, CLI, canvas, a11y."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import zipfile

import pytest
from conftest import make_orchestrator, make_settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core import plugins as plugin_core
from omniforge.core.alerts import AlertEngine
from omniforge.core.billing import Billing
from omniforge.core.knowledge_import import ImportError_ as ImportProblem
from omniforge.core.knowledge_import import parse_upload
from omniforge.core.mailer import ConsoleMailer
from omniforge.core.review import Reviewer
from omniforge.core.store import RunStore
from omniforge.core.tasks import Run, Task
from omniforge.skills.base import Skill

PASSWORD = "hunter2hunter2"


# ------------------------------------------------------------- B9 rubric
async def test_rubric_rejects_thin_output(tmp_path):
    settings = make_settings(tmp_path)
    reviewer = Reviewer(settings)
    skill = Skill(name="strict", department="qa", description="d", min_words=50,
                  must_contain=("exit criteria",))
    task = Task(title="t", department="qa", skill="strict")
    thin = await reviewer.review(task, "# Title\n\n- short line\n- another line", skill)
    assert not thin.passed
    assert any("rubric" in issue or "word" in issue for issue in thin.issues)
    good = await reviewer.review(
        task,
        "# Title\n\n" + "\n".join(f"- point {i} with enough words to pass" for i in range(8))
        + "\n\nexit criteria: all green",
        skill,
    )
    assert good.passed


def test_library_carries_rubrics():
    from omniforge.agents.roles import build_skill_registry

    skills = build_skill_registry()
    assert skills.get("test_plan").min_words >= 40
    assert skills.get("security_review").must_contain


# --------------------------------------------- B14 provider cap mid-run
async def test_run_stops_when_the_provider_budget_is_gone(tmp_path):
    settings = make_settings(tmp_path, provider_budgets={"openai": 0.000001})
    store = RunStore(settings.db_path)
    billing = Billing(settings, store)
    billing.record(user_id=None, run_id="seed", model="gpt-4o-mini",
                   input_tokens=1_000_000, output_tokens=0,
                   ts=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
    orch = make_orchestrator(tmp_path, provider_budgets={"openai": 0.000001})
    orch.billing = billing
    run = await orch.run_to_completion("Build a mobile game", auto_approve=True)
    assert run.status.value == "failed"
    assert "budget" in (run.error or "").lower()
    store.close()


async def test_delegation_is_refused_when_the_budget_is_almost_spent(tmp_path):
    from omniforge.core.budget import Budget

    orch = make_orchestrator(tmp_path, budget_tokens=1000)
    run = Run(goal="g")
    orch.runs[run.id] = run
    budget = Budget(limit_tokens=1000)
    budget.add(950, 0)
    orch._budgets[run.id] = budget
    from omniforge.core.tasks import Plan

    run.plan = Plan(goal="g", tasks=[Task(title="parent", department="research",
                                          skill="data_profile")])
    result = orch._delegate(run, run.plan.tasks[0], {"title": "child"})
    assert result["ok"] is False and "budget" in result["reason"]


# --------------------------------------------------- B4 backup schedule
def test_backup_schedule_fires(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        created = client.post("/api/schedules", json={
            "name": "nightly", "job": "backup", "every_seconds": 60}).json()
        assert created["job"] == "backup"
        later = (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 600)) + "+00:00")
        fired = client.post("/api/schedules/tick", params={"now": later}).json()["fired"]
        assert fired and fired[0]["backup"]
        backups = list((tmp_path / "backups").glob("*.zip"))
        assert backups


def test_schedule_job_validation(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.post("/api/schedules", json={"name": "no goal"}).status_code == 400
        assert client.post("/api/schedules", json={
            "name": "bad job", "job": "explode"}).status_code == 422


# --------------------------------------------------- B6 import formats
def test_parse_upload_formats():
    assert parse_upload("a.md", b"# Title\nbody")["documents"][0]["title"] == "a.md"
    csv_bytes = b"title,text\nalpha,first body\nbeta,second body\n"
    docs = parse_upload("x.csv", csv_bytes)["documents"]
    assert [d["title"] for d in docs] == ["alpha", "beta"]
    json_bytes = json.dumps({"documents": [{"title": "j", "text": "body"}]}).encode()
    assert parse_upload("x.json", json_bytes)["documents"][0]["title"] == "j"
    with pytest.raises(ImportProblem):
        parse_upload("x.pdf", b"%PDF-1.4")
    with pytest.raises(ImportProblem):
        parse_upload("x.csv", b"")


def test_zip_import_endpoint(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("notes.md", "vacuum tuning notes")
        bundle.writestr("skip.exe", b"\x00\x01")
    with TestClient(create_app(make_settings(tmp_path))) as client:
        out = client.post("/api/knowledge/import-file",
                          files={"file": ("pack.zip", buffer.getvalue(), "application/zip")}).json()
        assert out["added"] == 1 and out["skipped"][0]["file"] == "skip.exe"
        hits = client.get("/api/knowledge/search", params={"q": "vacuum"}).json()["hits"]
        assert hits
        assert client.post("/api/knowledge/import-file",
                           files={"file": ("bad.zip", b"nope", "application/zip")}
                           ).status_code == 400


# ------------------------------------------------ B5 severity / quiet
def test_quiet_hours_and_critical_bypass(tmp_path):
    settings = make_settings(tmp_path, alert_email="ops@example.com")
    store = RunStore(settings.db_path)
    mailer = ConsoleMailer()
    engine = AlertEngine(store, settings, mailer=mailer)
    engine.create(name="quiet", metric="run_tokens", comparator="gt", threshold=-1,
                  cooldown_seconds=0, quiet_start=0, quiet_end=23, severity="warning")
    assert asyncio.run(engine.evaluate(None)) == []

    engine.create(name="loud", metric="run_tokens", comparator="gt", threshold=-1,
                  cooldown_seconds=0, quiet_start=0, quiet_end=23, severity="critical")
    fired = asyncio.run(engine.evaluate(None))
    assert [f["name"] for f in fired] == ["loud"]
    with pytest.raises(ValueError):
        engine.create(name="bad", metric="run_tokens", comparator="gt", threshold=1,
                      severity="urgent")
    store.close()


# ------------------------------------------------ B12 public-key signing
def _keypair():
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())).decode()
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return private, public


def _package(manifest: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
    return buffer.getvalue()


def test_ed25519_signed_plugin_is_trusted(tmp_path):
    private, public = _keypair()
    manifest = {"name": "signed", "version": "1.0.0", "permissions": ["net.read"],
                "skills": [{"name": "signed_skill", "department": "research",
                            "description": "x"}]}
    signed = dict(manifest, signature=plugin_core.sign_public(manifest, private))
    store = RunStore(tmp_path / "db.sqlite")
    record = plugin_core.install(store, tmp_path, _package(signed),
                                 make_settings(tmp_path, plugin_public_key=public),
                                 None, "2026-01-01T00:00:00+00:00")
    assert record["trust"] == "verified"
    assert plugin_core.verify_public_signature(signed, public) is True
    assert plugin_core.verify_public_signature(dict(signed, signature="AAAA"), public) is False
    store.close()


def test_revoked_plugin_cannot_be_installed(tmp_path):
    private, public = _keypair()
    manifest = {"name": "revoked", "version": "2.0.0", "permissions": ["net.read"],
                "skills": [{"name": "r_skill", "department": "research", "description": "x"}]}
    signed = dict(manifest, signature=plugin_core.sign_public(manifest, private))
    store = RunStore(tmp_path / "db.sqlite")
    settings = make_settings(tmp_path, plugin_public_key=public)
    plugin_core.install(store, tmp_path, _package(signed), settings, None,
                        "2026-01-01T00:00:00+00:00")
    store.revoke_plugin("revoked", "2.0.0", "malicious", "2026-01-02T00:00:00+00:00")
    with pytest.raises(plugin_core.PluginError):
        plugin_core.install(store, tmp_path, _package(signed), settings, None,
                            "2026-01-03T00:00:00+00:00")
    store.close()


def test_revocation_endpoints(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.post("/api/plugins/revocations", json={
            "name": "bad", "version": "1.0.0", "reason": "test"}).status_code == 201
        assert client.get("/api/plugins/revocations").json()["revocations"][0]["name"] == "bad"


# ------------------------------------------------ B8 debate judge
async def test_judge_can_override_the_review_score(tmp_path):
    class Judge:
        name = "judge-model"
        model = "judge-model"

        async def plan(self, goal, catalog):
            from omniforge.core.llm import heuristic_plan

            return heuristic_plan(goal, catalog)

        async def complete(self, system, prompt, json_mode=False):
            from omniforge.core.llm import LLMResponse

            if "Pick the best candidate" in prompt:
                return LLMResponse(text='{"index": 1, "reason": "clearest"}', model="judge-model")
            return LLMResponse(text="# Model answer\n\n- a considered point\n- another point\n- third",
                               model="judge-model")

        async def stream(self, system, prompt, on_token, *, json_mode=False):
            return await self.complete(system, prompt, json_mode)

    from omniforge.agents.roles import build_skill_registry
    from omniforge.skills.base import Skill as S

    calls = {"n": 0}

    def handler(ctx):
        calls["n"] += 1
        return f"# Variant {calls['n']}\n\n- point one detail\n- point two detail\n- three"

    skills = build_skill_registry()
    skills.register("judge_demo", S(name="judge_demo", department="research",
                                    description="d", handler=handler))
    orch = make_orchestrator(tmp_path, skills=skills, llm=Judge(), debate_candidates=2,
                             debate_judge=True, prefer_generators=False,
                             llm_model="judge-model")
    run = await orch.run_to_completion("pick one", auto_approve=True)
    debated = [e for e in orch.bus.history(run_id=run.id) if e["type"] == "task.debated"]
    assert debated and debated[0]["payload"]["decided_by"].startswith("judge")


# ------------------------------------------------ B11 streamed summary
async def test_summary_tokens_are_streamed(tmp_path):
    from omniforge.core.llm import BaseLLM

    class Streamer(BaseLLM):
        name = "streamer"
        model = "streamer"

        async def plan(self, goal, catalog):
            from omniforge.core.llm import heuristic_plan

            return heuristic_plan(goal, catalog)

        async def complete(self, system, prompt, json_mode=False):
            from omniforge.core.llm import LLMResponse

            return LLMResponse(text="summary body for the handoff, long enough to pass",
                               model="streamer")

        async def stream(self, system, prompt, on_token, *, json_mode=False):
            from omniforge.core.llm import LLMResponse

            for piece in ("summary ", "body for the handoff"):
                result = on_token(piece)
                if hasattr(result, "__await__"):
                    await result
            return LLMResponse(text="summary body for the handoff, long enough to pass",
                               model="streamer")

    # review is off here: this test is about the streamed summary, not the rubric
    orch = make_orchestrator(tmp_path, llm=Streamer(), prefer_generators=False,
                             review_enabled=False)
    run = await orch.run_to_completion("Build a mobile game", auto_approve=True)
    assert run.status.value == "done", run.error
    events = [e for e in orch.bus.history(run_id=run.id) if e["type"] == "run.token"]
    assert events and "summary" in "".join(e["payload"]["delta"] for e in events)


# ------------------------------------------------ B1 more tunables
def test_more_settings_are_tunable(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        tunable = client.get("/api/settings").json()["tunable"]
        for key in ("http_allowlist", "cors_origins", "run_timeout", "max_tasks_per_run",
                    "alert_on_failure", "debate_judge"):
            assert key in tunable
        applied = client.patch("/api/settings", json={
            "values": {"http_allowlist": "example.com, api.test", "run_timeout": 120}}).json()
        assert list(applied["applied"]["http_allowlist"]) == ["example.com", "api.test"]
        assert client.app.state.settings.run_timeout == 120


# ------------------------------------------------ A6 + B2 dashboard
def test_dashboard_canvas_and_a11y(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        page = client.get("/").text
        assert "startNodeDrag" in page and "pointerdown" in page
        assert 'aria-live="polite"' in page
        assert 'role="status"' in page
        assert "focus-visible" in page


# ------------------------------------------------ B16 CLI
def test_cli_completions_and_watch(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "OMNIFORGE_DATA_DIR": str(tmp_path), "PYTHONPATH": str(root)}
    done = subprocess.run([sys.executable, "-m", "omniforge.cli", "completions", "bash"],
                          capture_output=True, text=True, env=env, cwd=str(root), timeout=60)
    assert done.returncode == 0 and "complete -F _omniforge omniforge" in done.stdout
    watch = subprocess.run([sys.executable, "-m", "omniforge.cli", "run",
                            "Write a test plan", "--watch"],
                           capture_output=True, text=True, env=env, cwd=str(root), timeout=180)
    assert watch.returncode == 0
    assert "task.finished" in watch.stdout
