"""The native PostgreSQL store, against a real server (skipped when unavailable)."""
from __future__ import annotations

import pytest

pytest.importorskip("pgserver")
pytest.importorskip("psycopg")

from conftest import make_settings  # noqa: E402

from omniforge.core import pg  # noqa: E402
from omniforge.core.orchestrator import Orchestrator  # noqa: E402
from omniforge.core.store_pg import PostgresStore  # noqa: E402


@pytest.fixture(scope="module")
def pg_url():
    import tempfile

    import pgserver

    return pgserver.get_server(tempfile.mkdtemp()).get_uri()


@pytest.fixture
def store(pg_url):
    s = PostgresStore(pg_url)
    yield s
    s.close()


def test_schema_covers_every_sqlite_table():
    from omniforge.core.store import SCHEMA

    statements = " ".join(pg.schema_statements())
    sqlite_tables = {
        line.split("CREATE TABLE IF NOT EXISTS ")[1].split(" ")[0]
        for line in SCHEMA.splitlines() if "CREATE TABLE IF NOT EXISTS" in line
    }
    for table in sqlite_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in statements
    assert "AUTOINCREMENT" not in statements
    assert "BIGSERIAL" in statements


def test_users_sessions_and_api_keys(store):
    from omniforge.core import auth

    user = auth.register_user(store, "pg@example.com", "hunter2hunter2", "PG")
    settings = make_settings("/tmp")
    token, _ = auth.login(store, settings, "pg@example.com", "hunter2hunter2")
    assert store.get_session(auth.token_hash(token))["user_id"] == user["id"]
    key, record = auth.issue_api_key(store, user["id"], "pg-key")
    assert store.find_api_key(auth.token_hash(key))["id"] == record["id"]
    assert store.stats()["users"] >= 1


async def test_full_run_lifecycle_on_postgres(pg_url):
    settings = make_settings("/tmp", mock_llm=True, data_dir="/tmp")
    from omniforge.agents.roles import build_agent_registry, build_skill_registry, build_tool_registry
    from omniforge.core.llm import MockLLM

    store = PostgresStore(pg_url)
    skills = build_skill_registry()
    orch = Orchestrator(
        settings=settings, llm=MockLLM(), skills=skills, agents=build_agent_registry(skills),
        tools=build_tool_registry("/tmp", settings), store=store,
    )
    run = await orch.run_to_completion("Build a mobile puzzle game", auto_approve=True)
    assert run.status.value == "done"
    assert store.get_run(run.id)["plan"]["tasks"]
    assert store.latest_checkpoint(run.id)["seq"] >= 2
    assert store.list_memory(run_id=run.id)
    assert store.list_provenance(run_id=run.id)
    assert store.documents()
    stats = store.stats()
    assert stats["runs"] >= 1 and stats["tasks"] >= 5
    store.close()


async def test_resume_and_idempotency_on_postgres(pg_url):
    settings = make_settings("/tmp", mock_llm=True, data_dir="/tmp")
    from omniforge.agents.roles import build_agent_registry, build_skill_registry, build_tool_registry
    from omniforge.core.llm import MockLLM

    store = PostgresStore(pg_url)
    skills = build_skill_registry()
    orch = Orchestrator(
        settings=settings, llm=MockLLM(), skills=skills, agents=build_agent_registry(skills),
        tools=build_tool_registry("/tmp", settings), store=store,
    )
    first = await orch.submit("Write a test plan", auto_approve=True, idempotency_key="pg-1")
    second = await orch.submit("Write a test plan", auto_approve=True, idempotency_key="pg-1")
    assert first.id == second.id
    await orch.wait(first.id)
    resumed = await orch.resume(first.id)
    await orch.wait(resumed.id)
    assert resumed.status.value == "done"
    store.close()


def test_postgres_store_survives_a_reconnect(pg_url):
    first = PostgresStore(pg_url)
    first.create_project("Persisted", "2026-01-01T00:00:00+00:00")
    runs_before = first.stats()["runs"]
    first.close()

    second = PostgresStore(pg_url, create_schema=False)
    assert second.stats()["runs"] == runs_before
    assert any(p["name"] == "Persisted" for p in second.list_projects())
    second.close()


def test_store_from_url_returns_postgres(pg_url):
    store = pg.store_from_url(pg_url)
    assert isinstance(store, PostgresStore)
    assert store.backend() == "postgres"
    store.close()


def test_workflow_and_alert_tables_work(store):
    wf = store.save_workflow("pg wf", "do {{x}}", '{"tasks": []}', "[]",
                             "2026-01-01T00:00:00+00:00")
    assert store.get_workflow(wf["id"])["name"] == "pg wf"
    store.add_alert_rule({"id": "rule_pg", "name": "r", "metric": "run_tokens",
                          "comparator": "gt", "threshold": 1, "channel": "email",
                          "cooldown_seconds": 60, "created_at": "2026-01-01T00:00:00+00:00"})
    assert store.get_alert_rule("rule_pg")["cooldown_seconds"] == 60
