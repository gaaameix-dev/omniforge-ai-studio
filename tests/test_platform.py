"""Production behaviours: backups, checkpoints, resume, control, idempotency."""
from __future__ import annotations

import asyncio
import json

import pytest
from conftest import FixedPlanner, make_orchestrator

from omniforge.core.backup import create_backup, restore_backup, verify_backup
from omniforge.core.store import RunStore
from omniforge.core.tasks import TaskStatus


# ------------------------------------------------------------------- backups
def _seed(tmp_path):
    store = RunStore(tmp_path / "omniforge.db")
    store.create_user("a@example.com", "hash", "A", "owner", "2026-01-01")
    store.add_audit("a@example.com", "test", None, "seed", None, "2026-01-01")
    (tmp_path / "artifacts" / "run_x").mkdir(parents=True)
    (tmp_path / "artifacts" / "run_x" / "summary.md").write_text("# hi")
    return store


def test_backup_verify_and_restore(tmp_path):
    store = _seed(tmp_path)
    store.close()
    info = create_backup(tmp_path)
    report = verify_backup(info["archive"])
    assert report["ok"] and report["checked"] >= 2 and not report["problems"]

    (tmp_path / "omniforge.db").unlink()
    result = restore_backup(info["archive"], tmp_path)
    assert result["verified"] is True
    restored = RunStore(tmp_path / "omniforge.db")
    assert restored.stats()["users"] == 1
    assert (tmp_path / "artifacts" / "run_x" / "summary.md").read_text() == "# hi"
    restored.close()


def test_verify_detects_tampered_content(tmp_path):
    import zipfile

    store = _seed(tmp_path)
    store.close()
    info = create_backup(tmp_path)
    archive = info["archive"]
    # rebuild the archive with one file's bytes altered but a valid zip structure
    with zipfile.ZipFile(archive) as bundle:
        entries = {name: bundle.read(name) for name in bundle.namelist()}
    entries["artifacts/run_x/summary.md"] = b"# tampered"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, payload in entries.items():
            bundle.writestr(name, payload)
    report = verify_backup(archive)
    assert report["ok"] is False
    assert any("hash mismatch" in problem for problem in report["problems"])


def test_verify_rejects_a_non_zip(tmp_path):
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"this is not a zip file at all")
    report = verify_backup(broken)
    assert report["ok"] is False and report["problems"]


def test_restore_refuses_to_clobber_without_force(tmp_path):
    store = _seed(tmp_path)
    store.close()
    info = create_backup(tmp_path)
    with pytest.raises(FileExistsError):
        restore_backup(info["archive"], tmp_path)
    forced = restore_backup(info["archive"], tmp_path, force=True)
    assert forced["verified"] is True
    assert (tmp_path / "omniforge.db.bak").exists()


def test_backup_without_a_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_backup(tmp_path)


# --------------------------------------------------------------- checkpoints
async def test_checkpoints_are_written_per_wave(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.run_to_completion("Build a mobile puzzle game", auto_approve=True)
    checkpoint = orch.store.latest_checkpoint(run.id)
    assert checkpoint is not None
    plan = json.loads(checkpoint["plan"])
    assert len(plan["tasks"]) == len(run.plan.tasks)
    assert checkpoint["seq"] >= 2


async def test_resume_continues_a_run_from_its_checkpoint(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.run_to_completion("Design a postgres schema", auto_approve=True)
    # simulate a crash: the record says running, the process is gone
    orch.store._conn.execute("UPDATE runs SET status='running', error=NULL WHERE id=?", (run.id,))
    orch.store._conn.execute(
        "UPDATE tasks SET status='pending', result=NULL WHERE run_id=? AND status='done'", (run.id,)
    )
    orch.store._conn.commit()

    fresh = make_orchestrator(tmp_path)
    resumed = await fresh.resume(run.id)
    await fresh.wait(resumed.id)
    assert resumed.status.value == "done"
    assert all(t.status is TaskStatus.DONE for t in resumed.plan.tasks)
    assert fresh.metrics.counters["runs.resumed"] == 1


async def test_resume_without_checkpoint_is_rejected(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.submit("Build a mobile game", auto_approve=True)
    await orch.wait(run.id)
    orch.store._conn.execute("DELETE FROM checkpoints WHERE run_id=?", (run.id,))
    orch.store._conn.commit()
    with pytest.raises(ValueError):
        await orch.resume(run.id)
    with pytest.raises(KeyError):
        await orch.resume("run_missing")


# ------------------------------------------------------------------- control
async def test_pause_holds_the_scheduler_and_unpause_resumes(tmp_path):
    orch = make_orchestrator(tmp_path)
    orch.pause()
    assert orch.control_state()["paused"] is True
    run = await orch.submit("Build a mobile puzzle game", auto_approve=True)
    await asyncio.sleep(0.3)
    assert not orch._jobs[run.id].done()
    assert all(t.status is not TaskStatus.DONE for t in run.plan.tasks)
    orch.unpause()
    await orch.wait(run.id)
    assert run.status.value == "done"


async def test_emergency_stop_blocks_new_runs_until_cleared(tmp_path):
    orch = make_orchestrator(tmp_path)
    result = await orch.emergency_stop("test stop")
    assert result["stopped"] is True and orch.stopped is True
    with pytest.raises(RuntimeError):
        await orch.submit("Build something", auto_approve=True)
    orch.clear_stop()
    run = await orch.run_to_completion("Write a test plan", auto_approve=True)
    assert run.status.value == "done"


# --------------------------------------------------------------- idempotency
async def test_idempotency_key_returns_the_same_run(tmp_path):
    orch = make_orchestrator(tmp_path)
    first = await orch.submit("Build a mobile game", auto_approve=True, idempotency_key="abc")
    second = await orch.submit("Build a mobile game", auto_approve=True, idempotency_key="abc")
    third = await orch.submit("Build a mobile game", auto_approve=True, idempotency_key="def")
    assert first.id == second.id != third.id
    assert orch.metrics.counters["runs.deduplicated"] == 1
    await orch.wait(first.id)


async def test_priority_orders_ready_tasks(tmp_path):
    plan = [
        {"key": "a", "title": "Low", "department": "research", "skill": "data_profile",
         "depends_on": [], "priority": 1},
        {"key": "b", "title": "High", "department": "research", "skill": "data_profile",
         "depends_on": [], "priority": 9},
    ]
    orch = make_orchestrator(tmp_path, max_parallel=1, llm=FixedPlanner(plan))
    run = await orch.run_to_completion("Profile the dataset", auto_approve=True)
    started = [e["payload"]["title"] for e in orch.bus.history(run_id=run.id)
               if e["type"] == "task.started"]
    assert started[0] == "High", started
    assert {t.priority for t in run.plan.tasks} == {1, 9}


# ------------------------------------------------------------------- monitor
async def test_metrics_and_monitor_state(tmp_path):
    orch = make_orchestrator(tmp_path)
    await orch.run_to_completion("Build a mobile game", auto_approve=True)
    state = orch.control_state()
    assert state["paused"] is False and state["stopped"] is False
    assert orch.metrics.counters["runs.submitted"] >= 1
    assert orch.store.stats()["runs"] >= 1
