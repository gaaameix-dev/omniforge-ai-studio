"""One-way mirror of the SQLite store into PostgreSQL.

Why a mirror and not a replacement: the run store is SQLite because a single file
is the right default for a self-hosted tool. Reporting, dashboards and BI want a
real database, so this copies runs, tasks, events, documents, usage, audit and
tool calls into PostgreSQL where they can be queried with SQL.

It is verified against a real server in the test suite (embedded PostgreSQL). The
native Postgres store is still open; see ROADMAP.md.
"""
from __future__ import annotations

import json
from typing import Any

from . import pg

TABLES = ("runs", "tasks", "events", "documents", "usage", "audit", "tool_calls")


def _upsert_sql(table: str, columns: list[str], conflict: str) -> str:
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in columns if c != conflict)
    return (f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}")


RUN_COLUMNS = ["id", "goal", "status", "strategy", "created_at", "updated_at", "auto_approve",
               "summary", "error", "tokens", "cost_usd", "project_id", "idempotency_key",
               "priority", "workspace_id", "meta"]
TASK_COLUMNS = ["id", "run_id", "seq", "title", "description", "department", "skill", "agent_id",
                "status", "result", "error", "attempts", "requires_approval", "depends_on",
                "started_at", "finished_at", "review_score", "parent_id"]


def sync(store, url: str, *, limit_runs: int = 500, verbose: bool = False) -> dict[str, Any]:
    """Copy the SQLite contents into PostgreSQL. Safe to run repeatedly."""
    conn = pg.connect(url)
    try:
        created = pg.init_schema(conn)
        counts: dict[str, int] = {}
        runs = store.list_runs(limit=limit_runs)
        with conn.cursor() as cur:
            for run in runs:
                record = store.get_run(run["id"])
                if record is None:
                    continue
                cur.execute(
                    _upsert_sql("runs", RUN_COLUMNS, "id"),
                    [record["id"], record["goal"], record["status"],
                     (record.get("plan") or {}).get("strategy"), record["created_at"],
                     record["updated_at"], int(record.get("auto_approve") or 0),
                     record.get("summary"), record.get("error"), int(record.get("tokens") or 0),
                     float(record.get("cost_usd") or 0), record.get("project_id"),
                     record.get("idempotency_key"), int(record.get("priority") or 5),
                     record.get("workspace_id"), json.dumps(record.get("meta") or {})],
                )
                counts["runs"] = counts.get("runs", 0) + 1

                tasks = (record.get("plan") or {}).get("tasks", [])
                for seq, task in enumerate(tasks):
                    cur.execute(
                        _upsert_sql("tasks", TASK_COLUMNS, "id"),
                        [task["id"], record["id"], seq, task.get("title", ""),
                         task.get("description"), task.get("department"), task.get("skill"),
                         task.get("agent_id"), task.get("status", "pending"), task.get("result"),
                         task.get("error"), int(task.get("attempts") or 0),
                         int(bool(task.get("requires_approval"))),
                         json.dumps(task.get("depends_on") or []), task.get("started_at"),
                         task.get("finished_at"), task.get("review_score"), task.get("parent_id")],
                    )
                    counts["tasks"] = counts.get("tasks", 0) + 1

            events = []
            for run in runs:
                events.extend(store.events(run["id"], limit=500))
            for event in events:
                cur.execute(
                    "INSERT INTO events (run_id, task_id, type, payload, ts) VALUES (%s,%s,%s,%s,%s)",
                    (event.get("run_id"), event.get("task_id"), event["type"],
                     json.dumps(event.get("payload") or {}), event["ts"]),
                )
                counts["events"] = counts.get("events", 0) + 1

            for document in store.documents(limit=2000):
                cur.execute(
                    "INSERT INTO documents (id, run_id, title, text, ts) VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT (id) DO UPDATE SET text=EXCLUDED.text, title=EXCLUDED.title",
                    (document["id"], document["run_id"], document["title"], document["text"],
                     document["ts"]),
                )
                counts["documents"] = counts.get("documents", 0) + 1

            usage = store.usage_summary(None)
            if usage["entries"]:
                for row in _usage_rows(store):
                    cur.execute(
                        "INSERT INTO usage (user_id, run_id, model, input_tokens, output_tokens, "
                        "cost_usd, ts) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (row["user_id"], row["run_id"], row["model"], row["input_tokens"],
                         row["output_tokens"], row["cost_usd"], row["ts"]),
                    )
                    counts["usage"] = counts.get("usage", 0) + 1

            for entry in store.list_audit(limit=5000):
                cur.execute(
                    "INSERT INTO audit (actor, action, target, detail, ip, ts) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (entry["actor"], entry["action"], entry["target"], entry["detail"],
                     entry["ip"], entry["ts"]),
                )
                counts["audit"] = counts.get("audit", 0) + 1

            for call in store.tool_calls(limit=5000):
                cur.execute(
                    "INSERT INTO tool_calls (run_id, agent_id, tool, ok, duration_s, error, "
                    "args_summary, ts) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (call["run_id"], call["agent_id"], call["tool"], call["ok"],
                     call["duration_s"], call["error"], call["args_summary"], call["ts"]),
                )
                counts["tool_calls"] = counts.get("tool_calls", 0) + 1
        return {"schema_statements": created, "copied": counts,
                "note": "one-way mirror; SQLite remains the source of truth"}
    finally:
        conn.close()


def _usage_rows(store) -> list[dict]:
    with store._lock:
        rows = store._conn.execute("SELECT * FROM usage ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def verify(url: str) -> dict[str, Any]:
    """Open the database and report what is there."""
    conn = pg.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
            counts = {}
            for table in TABLES:
                cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 - fixed table names
                counts[table] = cur.fetchone()[0]
        return {"ok": True, "server": version.split(",")[0], "rows": counts}
    finally:
        conn.close()
