"""Durability: the SQLite record must survive a restart and answer for old runs."""
from __future__ import annotations

from omniforge.core.store import RunStore
from omniforge.core.tasks import Plan, Run, RunStatus, Task, TaskStatus


def _run() -> Run:
    task = Task(title="Research brief", department="research", skill="web_research")
    task.status = TaskStatus.DONE
    task.result = "# brief\n\n- point one"
    plan = Plan(goal="ship something", tasks=[task], strategy="fixed")
    run = Run(goal="ship something", plan=plan, auto_approve=True)
    run.status = RunStatus.DONE
    run.summary = "done"
    run.tokens = 123
    run.cost_usd = 0.004
    return run


def test_roundtrip(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    run = _run()
    store.save_run(run)
    record = store.get_run(run.id)
    assert record is not None
    assert record["status"] == "done"
    assert record["tokens"] == 123
    assert record["plan"]["strategy"] == "fixed"
    assert record["plan"]["tasks"][0]["result"].startswith("# brief")
    assert record["counts"] == {"done": 1}
    store.close()


def test_upsert_updates_status_and_tasks(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    run = _run()
    store.save_run(run)
    run.status = RunStatus.FAILED
    run.error = "boom"
    run.plan.tasks[0].status = TaskStatus.FAILED
    store.save_run(run)
    record = store.get_run(run.id)
    assert record["status"] == "failed"
    assert record["error"] == "boom"
    assert len(record["plan"]["tasks"]) == 1
    assert record["plan"]["tasks"][0]["status"] == "failed"
    store.close()


def test_fail_interrupted(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    run = _run()
    run.status = RunStatus.RUNNING
    store.save_run(run)
    assert store.fail_interrupted() == 1
    record = store.get_run(run.id)
    assert record["status"] == "failed"
    assert "interrupted" in record["error"]
    assert store.fail_interrupted() == 0
    store.close()


def test_stats_and_listing(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    for _ in range(3):
        store.save_run(_run())
    stats = store.stats()
    assert stats["runs"] == 3
    assert stats["runs_by_status"] == {"done": 3}
    assert stats["tokens"] == 369
    assert len(store.list_runs(limit=2)) == 2
    assert len(store.list_runs(limit=5, status="done")) == 3
    assert store.list_runs(limit=5, status="failed") == []
    store.close()


def test_documents_and_events(tmp_path):
    from omniforge.core.events import Event

    store = RunStore(tmp_path / "db.sqlite")
    run = _run()
    store.save_run(run)
    store.add_document(run.id, "title", "body text about postgres indexes", "2026-01-01T00:00:00+00:00")
    store.add_event(Event(type="task.finished", run_id=run.id, task_id="t1", payload={"chars": 5}))
    assert store.stats()["documents"] == 1
    events = store.events(run.id)
    assert events[0]["type"] == "task.finished"
    assert events[0]["payload"]["chars"] == 5
    store.close()
