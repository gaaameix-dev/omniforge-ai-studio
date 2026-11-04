"""PostgreSQL seam.

The store is one class with one connection, so swapping the backend is contained.
This module ships the PostgreSQL schema and a thin adapter; if the driver is not
installed it fails with a clear message instead of pretending to work.

Status: the DDL and the factory are tested; a live PostgreSQL server is not
available in this environment, so the adapter itself is UNVERIFIED against a real
database. Treat it as a starting point, not a production claim.
"""
from __future__ import annotations

from pathlib import Path

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL, strategy TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, auto_approve INTEGER NOT NULL DEFAULT 0,
    summary TEXT, error TEXT, tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0, project_id TEXT,
    idempotency_key TEXT, priority INTEGER NOT NULL DEFAULT 5, workspace_id TEXT,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, title TEXT NOT NULL, description TEXT, department TEXT, skill TEXT,
    agent_id TEXT, status TEXT NOT NULL, result TEXT, error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, requires_approval INTEGER NOT NULL DEFAULT 0,
    depends_on TEXT NOT NULL DEFAULT '[]', started_at TEXT, finished_at TEXT,
    review_score DOUBLE PRECISION, parent_id TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY, run_id TEXT, task_id TEXT, type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}', ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY, run_id TEXT, title TEXT NOT NULL, text TEXT NOT NULL, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id BIGSERIAL PRIMARY KEY, user_id TEXT, run_id TEXT, model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id BIGSERIAL PRIMARY KEY, actor TEXT, action TEXT NOT NULL, target TEXT, detail TEXT,
    ip TEXT, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id BIGSERIAL PRIMARY KEY, run_id TEXT, agent_id TEXT, tool TEXT NOT NULL,
    ok INTEGER NOT NULL, duration_s DOUBLE PRECISION NOT NULL DEFAULT 0, error TEXT,
    args_summary TEXT, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_rules (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, metric TEXT NOT NULL, comparator TEXT NOT NULL,
    threshold DOUBLE PRECISION NOT NULL, channel TEXT NOT NULL, target TEXT,
    cooldown_seconds INTEGER NOT NULL DEFAULT 3600, enabled INTEGER NOT NULL DEFAULT 1,
    last_fired_at TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
"""


class PostgresUnavailable(RuntimeError):
    pass


def schema_statements() -> list[str]:
    return [stmt.strip() for stmt in SCHEMA_PG.split(";") if stmt.strip()]


def connect(url: str):
    """Open a psycopg connection, or explain exactly what is missing."""
    try:
        import psycopg  # noqa: PLC0415
    except ImportError as exc:
        raise PostgresUnavailable(
            "PostgreSQL support needs the psycopg driver: pip install 'psycopg[binary]'. "
            "SQLite remains the default and needs nothing."
        ) from exc
    try:
        return psycopg.connect(url, autocommit=True)
    except Exception as exc:  # noqa: BLE001 - surface the real reason
        raise PostgresUnavailable(f"cannot connect to PostgreSQL: {exc}") from exc


def init_schema(conn) -> int:
    with conn.cursor() as cur:
        for statement in schema_statements():
            cur.execute(statement)
    return len(schema_statements())


def store_from_url(url: str):
    """`sqlite:///path/to.db` -> RunStore; `postgres://...` -> clear error today."""
    from .store import RunStore

    if url.startswith("sqlite:///"):
        return RunStore(Path(url[len("sqlite:///"):]))
    if url.startswith("sqlite://"):
        return RunStore(Path(url[len("sqlite://"):]))
    if url.startswith(("postgres://", "postgresql://")):
        raise PostgresUnavailable(
            "PostgreSQL is planned but not wired into RunStore yet; this build uses SQLite. "
            "The schema lives in omniforge/core/pg.py for the migration."
        )
    raise ValueError(f"unsupported database URL: {url}")
