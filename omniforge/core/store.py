"""SQLite persistence: runs, tasks, events, knowledge documents.

One connection, WAL mode, short transactions. The store is the durable record -
in-memory state is a cache in front of it, so a restart loses no history and the
API can still answer for runs this process never saw.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    goal          TEXT NOT NULL,
    status        TEXT NOT NULL,
    strategy      TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    auto_approve  INTEGER NOT NULL DEFAULT 0,
    summary       TEXT,
    error         TEXT,
    tokens        INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    project_id    TEXT,
    idempotency_key TEXT,
    priority      INTEGER NOT NULL DEFAULT 5,
    workspace_id  TEXT,
    lease_owner   TEXT,
    lease_expires_at TEXT,
    meta          TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    workspace_id TEXT,
    owner_id   TEXT,
    token_quota INTEGER NOT NULL DEFAULT 0,
    meta       TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'member',
    created_at    TEXT NOT NULL,
    disabled      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen  TEXT
);
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    prefix       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS workspaces (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    owner_id   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS reset_tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mfa (
    user_id    TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    secret     TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mfa_backup_codes (
    user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    used      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, code_hash)
);
CREATE TABLE IF NOT EXISTS secrets (
    name       TEXT PRIMARY KEY,
    ciphertext TEXT NOT NULL,
    owner_id   TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    actor  TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    ip     TEXT,
    ts     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    plan       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflows (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    goal_template TEXT NOT NULL,
    plan          TEXT NOT NULL,
    params        TEXT NOT NULL DEFAULT '[]',
    version       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    owner_id      TEXT
);
CREATE TABLE IF NOT EXISTS hooks (
    id           TEXT PRIMARY KEY,
    token        TEXT NOT NULL UNIQUE,
    workflow_id  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    last_called_at TEXT
);
CREATE TABLE IF NOT EXISTS schedules (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'interval',
    every_seconds INTEGER NOT NULL DEFAULT 3600,
    at_hour      INTEGER,
    at_minute    INTEGER,
    goal         TEXT NOT NULL DEFAULT '',
    workflow_id  TEXT,
    project_id   TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    next_run_at  TEXT,
    last_run_at  TEXT,
    last_run_id  TEXT,
    created_at   TEXT NOT NULL,
    owner_id     TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT,
    kind     TEXT NOT NULL,
    target   TEXT,
    ok       INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    error    TEXT,
    ts       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id           TEXT PRIMARY KEY,
    project_id   TEXT,
    name         TEXT NOT NULL,
    content_type TEXT,
    bytes        INTEGER NOT NULL DEFAULT 0,
    sha256       TEXT NOT NULL,
    path         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    owner_id     TEXT
);
CREATE TABLE IF NOT EXISTS agent_memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT,
    agent_id   TEXT,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provenance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT,
    task_id       TEXT,
    agent_id      TEXT,
    artifact      TEXT NOT NULL,
    licence       TEXT,
    source        TEXT,
    checksum      TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT,
    run_id        TEXT,
    model         TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    ts            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS doc_vectors (
    document_id INTEGER PRIMARY KEY,
    vector      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'viewer',
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE TABLE IF NOT EXISTS alert_rules (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    metric           TEXT NOT NULL,
    comparator       TEXT NOT NULL,
    threshold        REAL NOT NULL,
    channel          TEXT NOT NULL,
    target           TEXT,
    cooldown_seconds INTEGER NOT NULL DEFAULT 3600,
    enabled          INTEGER NOT NULL DEFAULT 1,
    last_fired_at    TEXT,
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plugins (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    version      TEXT NOT NULL,
    manifest     TEXT NOT NULL,
    trust        TEXT NOT NULL DEFAULT 'unsigned',
    enabled      INTEGER NOT NULL DEFAULT 0,
    installed_at TEXT NOT NULL,
    path         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT,
    agent_id     TEXT,
    tool         TEXT NOT NULL,
    ok           INTEGER NOT NULL,
    duration_s   REAL NOT NULL DEFAULT 0,
    error        TEXT,
    args_summary TEXT,
    ts           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    title         TEXT NOT NULL,
    description   TEXT,
    department    TEXT,
    skill         TEXT,
    agent_id      TEXT,
    status        TEXT NOT NULL,
    result        TEXT,
    error         TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    depends_on    TEXT NOT NULL DEFAULT '[]',
    started_at    TEXT,
    finished_at   TEXT,
    review_score  REAL,
    parent_id     TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT,
    task_id  TEXT,
    type     TEXT NOT NULL,
    payload  TEXT NOT NULL DEFAULT '{}',
    ts       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT,
    title     TEXT NOT NULL,
    text      TEXT NOT NULL,
    ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_toolcalls_run ON tool_calls(run_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);
CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON checkpoints(run_id, seq DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_schedules_next ON schedules(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_hooks_token ON hooks(token);
CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
CREATE INDEX IF NOT EXISTS idx_memory_run ON agent_memory(run_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_provenance_run ON provenance(run_id);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_plugins_name ON plugins(name);
"""


class RunStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was created."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(runs)")}
        for column, ddl in (
            ("project_id", "TEXT"),
            ("idempotency_key", "TEXT"),
            ("priority", "INTEGER NOT NULL DEFAULT 5"),
            ("workspace_id", "TEXT"),
            ("lease_owner", "TEXT"),
            ("lease_expires_at", "TEXT"),
        ):
            if column not in columns:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")
        project_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(projects)")}
        for column, ddl in (("workspace_id", "TEXT"), ("owner_id", "TEXT"),
                            ("token_quota", "INTEGER NOT NULL DEFAULT 0")):
            if column not in project_columns:
                self._conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {ddl}")
        self._conn.execute("PRAGMA foreign_keys=ON")

    # ------------------------------------------------------------------ runs
    def save_run(self, run) -> None:
        meta = json.dumps(run.meta or {}, default=str)
        with self._lock:
            self._conn.execute(
                """INSERT INTO runs (id, goal, status, strategy, created_at, updated_at,
                                     auto_approve, summary, error, tokens, cost_usd, project_id,
                                     idempotency_key, priority, workspace_id, meta)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, updated_at=excluded.updated_at,
                     summary=excluded.summary, error=excluded.error,
                     tokens=excluded.tokens, cost_usd=excluded.cost_usd,
                     project_id=excluded.project_id, idempotency_key=excluded.idempotency_key,
                     priority=excluded.priority, workspace_id=excluded.workspace_id,
                     meta=excluded.meta""",
                (
                    run.id, run.goal, run.status.value,
                    run.plan.strategy if run.plan else None,
                    run.created_at, run.updated_at, int(run.auto_approve),
                    run.summary, run.error, int(getattr(run, "tokens", 0)),
                    float(getattr(run, "cost_usd", 0.0)),
                    getattr(run, "project_id", None),
                    getattr(run, "idempotency_key", None),
                    int(getattr(run, "priority", 5)),
                    getattr(run, "workspace_id", None), meta,
                ),
            )
            self._conn.execute("DELETE FROM tasks WHERE run_id=?", (run.id,))
            if run.plan:
                self._conn.executemany(
                    """INSERT INTO tasks (id, run_id, seq, title, description, department, skill,
                                          agent_id, status, result, error, attempts,
                                          requires_approval, depends_on, started_at, finished_at,
                                          review_score, parent_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [
                        (
                            t.id, run.id, idx, t.title, t.description, t.department, t.skill,
                            t.agent_id, t.status.value, t.result, t.error, t.attempts,
                            int(t.requires_approval), json.dumps(t.depends_on),
                            t.started_at, t.finished_at, t.review_score, t.parent_id,
                        )
                        for idx, t in enumerate(run.plan.tasks)
                    ],
                )
            self._conn.commit()

    def add_event(self, event) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (run_id, task_id, type, payload, ts) VALUES (?,?,?,?,?)",
                (event.run_id, event.task_id, event.type, json.dumps(event.payload, default=str), event.ts),
            )
            self._conn.commit()

    def list_runs(
        self, limit: int = 50, status: str | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("status=?")
            args.append(status)
        if project_id:
            clauses.append("project_id=?")
            args.append(project_id)
        sql = "SELECT * FROM runs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["meta"] = json.loads(data.get("meta") or "{}")
            data["counts"] = self._counts(row["id"])
            out.append(data)
        return out

    def _counts(self, run_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM tasks WHERE run_id=? GROUP BY status", (run_id,)
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def find_run_by_idempotency(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE idempotency_key=? ORDER BY created_at DESC, id DESC LIMIT 1", (key,)
            ).fetchone()
        return dict(row) if row else None

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None
            tasks = self._conn.execute(
                "SELECT * FROM tasks WHERE run_id=? ORDER BY seq", (run_id,)
            ).fetchall()
            events = self._conn.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        data = dict(row)
        data["meta"] = json.loads(data.get("meta") or "{}")
        data["plan"] = {
            "id": f"plan_{run_id}",
            "goal": row["goal"],
            "strategy": row["strategy"],
            "tasks": [
                {
                    "id": t["id"], "title": t["title"], "description": t["description"],
                    "department": t["department"], "skill": t["skill"], "agent_id": t["agent_id"],
                    "status": t["status"], "result": t["result"], "error": t["error"],
                    "attempts": t["attempts"], "requires_approval": bool(t["requires_approval"]),
                    "depends_on": json.loads(t["depends_on"] or "[]"),
                    "started_at": t["started_at"], "finished_at": t["finished_at"],
                    "review_score": t["review_score"], "parent_id": t["parent_id"],
                }
                for t in tasks
            ],
        }
        data["counts"] = {}
        for t in data["plan"]["tasks"]:
            data["counts"][t["status"]] = data["counts"].get(t["status"], 0) + 1
        data["events"] = [
            {"id": e["id"], "ts": e["ts"], "type": e["type"], "run_id": e["run_id"],
             "task_id": e["task_id"], "payload": json.loads(e["payload"] or "{}")}
            for e in events
        ]
        return data

    def events(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, limit)
            ).fetchall()
        return [
            {"id": r["id"], "ts": r["ts"], "type": r["type"], "task_id": r["task_id"],
             "payload": json.loads(r["payload"] or "{}")}
            for r in reversed(rows)
        ]

    def fail_interrupted(self) -> int:
        """Runs left mid-flight by a crash are marked, never silently resurrected."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status='failed', error=COALESCE(error, 'interrupted by restart') "
                "WHERE status IN ('planning','running','awaiting_approval')"
            )
            self._conn.commit()
            return cur.rowcount

    def stats(self) -> dict[str, Any]:
        with self._lock:
            runs = self._conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
            by_status = {
                r["status"]: r["n"]
                for r in self._conn.execute("SELECT status, COUNT(*) AS n FROM runs GROUP BY status")
            }
            tasks = self._conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
            tokens = self._conn.execute("SELECT COALESCE(SUM(tokens),0) AS n FROM runs").fetchone()["n"]
            cost = self._conn.execute("SELECT COALESCE(SUM(cost_usd),0) AS n FROM runs").fetchone()["n"]
            docs = self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
            calls = self._conn.execute("SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"]
            projects = self._conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
            users = self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            audit = self._conn.execute("SELECT COUNT(*) AS n FROM audit").fetchone()["n"]
            workflows = self._conn.execute("SELECT COUNT(*) AS n FROM workflows").fetchone()["n"]
            schedules = self._conn.execute("SELECT COUNT(*) AS n FROM schedules").fetchone()["n"]
            files = self._conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"]
            memory = self._conn.execute("SELECT COUNT(*) AS n FROM agent_memory").fetchone()["n"]
            plugins = self._conn.execute("SELECT COUNT(*) AS n FROM plugins").fetchone()["n"]
        return {
            "runs": runs,
            "runs_by_status": by_status,
            "tasks": tasks,
            "tokens": tokens,
            "cost_usd": round(cost, 6),
            "documents": docs,
            "tool_calls": calls,
            "projects": projects,
            "users": users,
            "audit_entries": audit,
            "workflows": workflows,
            "schedules": schedules,
            "files": files,
            "memory_entries": memory,
            "plugins": plugins,
        }

    # ------------------------------------------------------------- identity
    def create_user(self, email: str, password_hash: str, name: str, role: str, ts: str) -> dict:
        user_id = f"user_{os.urandom(6).hex()}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (id, email, password_hash, name, role, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, email.lower(), password_hash, name, role, ts),
            )
            self._conn.commit()
        return {"id": user_id, "email": email.lower(), "name": name, "role": role, "created_at": ts}

    def get_user(self, user_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email=?", (email.lower(),)
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, email, name, role, created_at, disabled FROM users ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_users(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])

    def set_user_password(self, user_id: str, password_hash: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
            self._conn.commit()

    def set_user_disabled(self, user_id: str, disabled: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET disabled=? WHERE id=?", (int(disabled), user_id))
            self._conn.commit()

    def delete_user(self, user_id: str) -> int:
        """Data deletion: the account and everything keyed to it goes away."""
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self._conn.execute("DELETE FROM api_keys WHERE user_id=?", (user_id,))
            self._conn.execute("DELETE FROM memberships WHERE user_id=?", (user_id,))
            cur = self._conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            self._conn.commit()
            return cur.rowcount

    # sessions
    def create_session(self, user_id: str, token_hash: str, created_at: str, expires_at: str) -> str:
        session_id = f"sess_{os.urandom(8).hex()}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at) VALUES (?,?,?,?,?)",
                (session_id, user_id, token_hash, created_at, expires_at),
            )
            self._conn.commit()
        return session_id

    def get_session(self, token_hash: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE token_hash=?", (token_hash,)
            ).fetchone()
        return dict(row) if row else None

    def touch_session(self, session_id: str, ts: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET last_seen=? WHERE id=?", (ts, session_id))
            self._conn.commit()

    def delete_session(self, token_hash: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
            self._conn.commit()
            return cur.rowcount

    def purge_expired_sessions(self, now: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            self._conn.commit()
            return cur.rowcount

    # api keys
    def create_api_key(self, user_id: str, name: str, key_hash: str, prefix: str, ts: str) -> str:
        key_id = f"key_{os.urandom(6).hex()}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO api_keys (id, user_id, name, key_hash, prefix, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (key_id, user_id, name, key_hash, prefix, ts),
            )
            self._conn.commit()
        return key_id

    def find_api_key(self, key_hash: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_keys WHERE key_hash=? AND revoked=0", (key_hash,)
            ).fetchone()
        return dict(row) if row else None

    def touch_api_key(self, key_id: str, ts: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (ts, key_id))
            self._conn.commit()

    def list_api_keys(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, prefix, created_at, last_used_at, revoked FROM api_keys "
                "WHERE user_id=? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: str, user_id: str | None = None) -> int:
        with self._lock:
            if user_id:
                cur = self._conn.execute(
                    "UPDATE api_keys SET revoked=1 WHERE id=? AND user_id=?", (key_id, user_id)
                )
            else:
                cur = self._conn.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))
            self._conn.commit()
            return cur.rowcount

    # workspaces
    def create_workspace(self, name: str, owner_id: str, ts: str) -> dict:
        workspace_id = f"ws_{os.urandom(5).hex()}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO workspaces (id, name, owner_id, created_at) VALUES (?,?,?,?)",
                (workspace_id, name, owner_id, ts),
            )
            self._conn.execute(
                "INSERT INTO memberships (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)",
                (workspace_id, owner_id, "owner", ts),
            )
            self._conn.commit()
        return {"id": workspace_id, "name": name, "owner_id": owner_id, "created_at": ts}

    def add_member(self, workspace_id: str, user_id: str, role: str, ts: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO memberships (workspace_id, user_id, role, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(workspace_id, user_id) DO UPDATE SET role=excluded.role",
                (workspace_id, user_id, role, ts),
            )
            self._conn.commit()

    def remove_member(self, workspace_id: str, user_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (workspace_id, user_id)
            )
            self._conn.commit()
            return cur.rowcount

    def member_role(self, workspace_id: str, user_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT role FROM memberships WHERE workspace_id=? AND user_id=?",
                (workspace_id, user_id),
            ).fetchone()
        return row["role"] if row else None

    def list_members(self, workspace_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.user_id, m.role, m.created_at, u.email, u.name FROM memberships m "
                "JOIN users u ON u.id = m.user_id WHERE m.workspace_id=? ORDER BY m.created_at",
                (workspace_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_workspaces(self, user_id: str | None = None) -> list[dict]:
        sql = (
            "SELECT w.*, (SELECT COUNT(*) FROM memberships m2 WHERE m2.workspace_id=w.id) AS members, "
            "(SELECT COUNT(*) FROM projects p WHERE p.workspace_id=w.id) AS projects "
            "FROM workspaces w"
        )
        args: list = []
        if user_id:
            sql += " JOIN memberships m ON m.workspace_id = w.id AND m.user_id=?"
            args.append(user_id)
        sql += " ORDER BY w.created_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def get_workspace(self, workspace_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------- password reset
    def create_reset_token(self, user_id: str, token_hash: str, created_at: str, expires_at: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO reset_tokens (user_id, token_hash, created_at, expires_at) VALUES (?,?,?,?)",
                (user_id, token_hash, created_at, expires_at),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def get_reset_token(self, token_hash: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reset_tokens WHERE token_hash=?", (token_hash,)
            ).fetchone()
        return dict(row) if row else None

    def consume_reset_token(self, token_hash: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reset_tokens SET used=1 WHERE token_hash=? AND used=0", (token_hash,)
            )
            self._conn.commit()
            return cur.rowcount

    def delete_sessions_for_user(self, user_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------- mfa
    def set_mfa_secret(self, user_id: str, secret: str, ts: str, enabled: bool = False) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO mfa (user_id, secret, enabled, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET secret=excluded.secret, enabled=excluded.enabled",
                (user_id, secret, int(enabled), ts),
            )
            self._conn.commit()

    def get_mfa(self, user_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM mfa WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def set_mfa_enabled(self, user_id: str, enabled: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE mfa SET enabled=? WHERE user_id=?", (int(enabled), user_id))
            self._conn.commit()

    def delete_mfa(self, user_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM mfa WHERE user_id=?", (user_id,))
            self._conn.execute("DELETE FROM mfa_backup_codes WHERE user_id=?", (user_id,))
            self._conn.commit()

    def add_backup_codes(self, user_id: str, code_hashes: list[str]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM mfa_backup_codes WHERE user_id=?", (user_id,))
            self._conn.executemany(
                "INSERT INTO mfa_backup_codes (user_id, code_hash) VALUES (?,?)",
                [(user_id, h) for h in code_hashes],
            )
            self._conn.commit()

    def use_backup_code(self, user_id: str, code_hash: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE mfa_backup_codes SET used=1 WHERE user_id=? AND code_hash=? AND used=0",
                (user_id, code_hash),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def count_unused_backup_codes(self, user_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM mfa_backup_codes WHERE user_id=? AND used=0", (user_id,)
            ).fetchone()
        return int(row["n"])

    # secrets (values are encrypted by core/vault.py before they get here)
    def put_secret(self, name: str, ciphertext: str, owner_id: str | None, ts: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO secrets (name, ciphertext, owner_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "ciphertext=excluded.ciphertext, updated_at=excluded.updated_at",
                (name, ciphertext, owner_id, ts, ts),
            )
            self._conn.commit()

    def get_secret(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM secrets WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def list_secrets(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, owner_id, created_at, updated_at FROM secrets ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_secret(self, name: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM secrets WHERE name=?", (name,))
            self._conn.commit()
            return cur.rowcount

    # audit
    def add_audit(self, actor: str | None, action: str, target: str | None,
                  detail: str | None, ip: str | None, ts: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit (actor, action, target, detail, ip, ts) VALUES (?,?,?,?,?,?)",
                (actor, action, target, detail, ip, ts),
            )
            self._conn.commit()

    def list_audit(self, limit: int = 100, action: str | None = None) -> list[dict]:
        sql = "SELECT * FROM audit"
        args: list = []
        if action:
            sql += " WHERE action=?"
            args.append(action)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # checkpoints
    def save_checkpoint(self, run_id: str, seq: int, plan_json: str, ts: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO checkpoints (run_id, seq, plan, created_at) VALUES (?,?,?,?)",
                (run_id, seq, plan_json, ts),
            )
            self._conn.execute(
                "DELETE FROM checkpoints WHERE run_id=? AND id NOT IN "
                "(SELECT id FROM checkpoints WHERE run_id=? ORDER BY id DESC LIMIT 20)",
                (run_id, run_id),
            )
            self._conn.commit()

    def latest_checkpoint(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checkpoints WHERE run_id=? ORDER BY seq DESC, id DESC LIMIT 1", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------- vectors
    def put_vector(self, document_id: int, vector_json: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO doc_vectors (document_id, vector) VALUES (?,?) "
                "ON CONFLICT(document_id) DO UPDATE SET vector=excluded.vector",
                (document_id, vector_json),
            )
            self._conn.commit()

    def get_vector(self, document_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT vector FROM doc_vectors WHERE document_id=?", (document_id,)
            ).fetchone()
        return row["vector"] if row else None

    def all_vectors(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT document_id, vector FROM doc_vectors").fetchall()
        return [dict(r) for r in rows]

    def documents_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, run_id, title, text, ts FROM documents WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------- usage
    def add_usage(self, user_id: str | None, run_id: str | None, model: str,
                  input_tokens: int, output_tokens: int, cost_usd: float, ts: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage (user_id, run_id, model, input_tokens, output_tokens, cost_usd, ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (user_id, run_id, model, int(input_tokens), int(output_tokens), float(cost_usd), ts),
            )
            self._conn.commit()

    def usage_summary(self, user_id: str | None = None, since: str | None = None) -> dict:
        clauses, args = [], []
        if user_id:
            clauses.append("user_id=?")
            args.append(user_id)
        if since:
            clauses.append("ts >= ?")
            args.append(since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            totals = self._conn.execute(
                f"SELECT COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o, "
                f"COALESCE(SUM(cost_usd),0) AS c, COUNT(*) AS n FROM usage{where}", args
            ).fetchone()
            by_model = self._conn.execute(
                f"SELECT model, COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o, "
                f"COALESCE(SUM(cost_usd),0) AS c, COUNT(*) AS n FROM usage{where} GROUP BY model", args
            ).fetchall()
        return {
            "input_tokens": int(totals["i"]),
            "output_tokens": int(totals["o"]),
            "tokens": int(totals["i"]) + int(totals["o"]),
            "cost_usd": round(float(totals["c"]), 6),
            "entries": int(totals["n"]),
            "by_model": [
                {"model": r["model"], "input_tokens": int(r["i"]), "output_tokens": int(r["o"]),
                 "cost_usd": round(float(r["c"]), 6), "entries": int(r["n"])}
                for r in by_model
            ],
        }

    # ----------------------------------------------------------- alert rules
    def add_alert_rule(self, data: dict[str, Any]) -> dict:
        with self._lock:
            self._conn.execute(
                "INSERT INTO alert_rules (id, name, metric, comparator, threshold, channel, target, "
                "cooldown_seconds, enabled, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (data["id"], data["name"], data["metric"], data["comparator"],
                 float(data["threshold"]), data["channel"], data.get("target"),
                 (3600 if data.get("cooldown_seconds") is None
                  else int(data["cooldown_seconds"])),
                 int(data.get("enabled", 1)),
                 data["created_at"]),
            )
            self._conn.commit()
        return data

    def list_alert_rules(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM alert_rules ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def get_alert_rule(self, rule_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
        return dict(row) if row else None

    def delete_alert_rule(self, rule_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))
            self._conn.commit()
            return cur.rowcount

    def touch_alert_rule(self, rule_id: str, ts: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE alert_rules SET last_fired_at=? WHERE id=?", (ts, rule_id))
            self._conn.commit()

    # --------------------------------------------------------------- plugins
    def add_plugin(self, data: dict[str, Any]) -> dict:
        with self._lock:
            self._conn.execute(
                "INSERT INTO plugins (id, name, version, manifest, trust, enabled, installed_at, path) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (data["id"], data["name"], data["version"], data["manifest"], data["trust"],
                 int(data.get("enabled", 0)), data["installed_at"], data["path"]),
            )
            self._conn.commit()
        return data

    def list_plugins(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM plugins ORDER BY installed_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_plugin(self, plugin_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,)).fetchone()
        return dict(row) if row else None

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE plugins SET enabled=? WHERE id=?", (int(enabled), plugin_id)
            )
            self._conn.commit()
            return cur.rowcount

    def delete_plugin(self, plugin_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM plugins WHERE id=?", (plugin_id,))
            self._conn.commit()
            return cur.rowcount

    # ----------------------------------------------------------------- files
    def add_file(self, data: dict[str, Any]) -> dict:
        with self._lock:
            self._conn.execute(
                "INSERT INTO files (id, project_id, name, content_type, bytes, sha256, path, "
                "created_at, owner_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    data["id"], data.get("project_id"), data["name"], data.get("content_type"),
                    int(data.get("bytes") or 0), data["sha256"], data["path"],
                    data["created_at"], data.get("owner_id"),
                ),
            )
            self._conn.commit()
        return data

    def list_files(self, project_id: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM files"
        args: list[Any] = []
        if project_id:
            sql += " WHERE project_id=?"
            args.append(project_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def get_file(self, file_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(row) if row else None

    def update_file_meta(self, file_id: str, size: int, sha256: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE files SET bytes=?, sha256=? WHERE id=?", (int(size), sha256, file_id)
            )
            self._conn.commit()
            return cur.rowcount

    def delete_file(self, file_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM files WHERE id=?", (file_id,))
            self._conn.commit()
            return cur.rowcount

    # ---------------------------------------------------------- agent memory
    def add_memory(self, run_id: str, agent_id: str, key: str, value: str, ts: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_memory (run_id, agent_id, key, value, created_at) VALUES (?,?,?,?,?)",
                (run_id, agent_id, key, value, ts),
            )
            self._conn.commit()

    def list_memory(self, run_id: str | None = None, agent_id: str | None = None,
                    limit: int = 100) -> list[dict]:
        clauses, args = [], []
        if run_id:
            clauses.append("run_id=?")
            args.append(run_id)
        if agent_id:
            clauses.append("agent_id=?")
            args.append(agent_id)
        sql = "SELECT * FROM agent_memory"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def delete_memory(self, memory_id: int) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM agent_memory WHERE id=?", (memory_id,))
            self._conn.commit()
            return cur.rowcount

    def clear_memory(self, run_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM agent_memory WHERE run_id=?", (run_id,))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------ provenance
    def add_provenance(self, data: dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO provenance (run_id, task_id, agent_id, artifact, licence, source, "
                "checksum, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    data.get("run_id"), data.get("task_id"), data.get("agent_id"),
                    data["artifact"], data.get("licence"), data.get("source"),
                    data.get("checksum"), data["created_at"],
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def list_provenance(self, run_id: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM provenance"
        args: list[Any] = []
        if run_id:
            sql += " WHERE run_id=?"
            args.append(run_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def set_licence(self, provenance_id: int, licence: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE provenance SET licence=? WHERE id=?", (licence, provenance_id)
            )
            self._conn.commit()
            return cur.rowcount

    # --------------------------------------------------------- agent scoring
    def agent_stats(self) -> list[dict]:
        """Per-agent throughput and quality, computed from task rows."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT agent_id, status, review_score, attempts, started_at, finished_at "
                "FROM tasks WHERE agent_id IS NOT NULL"
            ).fetchall()
        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            agent = row["agent_id"]
            bucket = buckets.setdefault(agent, {
                "agent_id": agent, "tasks": 0, "done": 0, "failed": 0, "skipped": 0,
                "scores": [], "attempts": [], "durations": [],
            })
            bucket["tasks"] += 1
            bucket[row["status"]] = bucket.get(row["status"], 0) + 1
            if row["review_score"] is not None:
                bucket["scores"].append(float(row["review_score"]))
            if row["attempts"]:
                bucket["attempts"].append(int(row["attempts"]))
            if row["started_at"] and row["finished_at"]:
                try:
                    start = datetime.fromisoformat(row["started_at"])
                    end = datetime.fromisoformat(row["finished_at"])
                    bucket["durations"].append((end - start).total_seconds())
                except ValueError:
                    pass
        out = []
        for bucket in buckets.values():
            scores = bucket.pop("scores")
            attempts = bucket.pop("attempts")
            durations = bucket.pop("durations")
            bucket["avg_review_score"] = round(sum(scores) / len(scores), 3) if scores else None
            bucket["avg_attempts"] = round(sum(attempts) / len(attempts), 2) if attempts else None
            bucket["avg_duration_s"] = round(sum(durations) / len(durations), 2) if durations else None
            bucket["reliability"] = (
                round(bucket["done"] / bucket["tasks"], 3) if bucket["tasks"] else 0.0
            )
            out.append(bucket)
        out.sort(key=lambda b: (-(b["avg_review_score"] or 0), -b["tasks"]))
        return out

    # ------------------------------------------------------------ workflows
    def save_workflow(self, name: str, goal_template: str, plan_json: str, params_json: str,
                      ts: str, owner_id: str | None = None) -> dict:
        workflow_id = f"wf_{os.urandom(5).hex()}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO workflows (id, name, goal_template, plan, params, version, created_at, "
                "updated_at, owner_id) VALUES (?,?,?,?,?,1,?,?,?)",
                (workflow_id, name, goal_template, plan_json, params_json, ts, ts, owner_id),
            )
            self._conn.commit()
        return {"id": workflow_id, "name": name, "created_at": ts, "version": 1}

    def get_workflow(self, workflow_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        return dict(row) if row else None

    def list_workflows(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, goal_template, params, version, created_at, updated_at FROM workflows "
                "ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_workflow(self, workflow_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM workflows WHERE id=?", (workflow_id,))
            self._conn.execute("DELETE FROM hooks WHERE workflow_id=?", (workflow_id,))
            self._conn.execute("UPDATE schedules SET workflow_id=NULL WHERE workflow_id=?", (workflow_id,))
            self._conn.commit()
            return cur.rowcount

    # ----------------------------------------------------------------- hooks
    def create_hook(self, workflow_id: str, token: str, ts: str) -> dict:
        hook_id = f"hook_{os.urandom(5).hex()}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO hooks (id, token, workflow_id, created_at) VALUES (?,?,?,?)",
                (hook_id, token, workflow_id, ts),
            )
            self._conn.commit()
        return {"id": hook_id, "workflow_id": workflow_id, "token": token, "created_at": ts}

    def get_hook_by_token(self, token: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM hooks WHERE token=? AND enabled=1", (token,)
            ).fetchone()
        return dict(row) if row else None

    def list_hooks(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, workflow_id, created_at, enabled, last_called_at FROM hooks "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def touch_hook(self, hook_id: str, ts: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE hooks SET last_called_at=? WHERE id=?", (ts, hook_id))
            self._conn.commit()

    def delete_hook(self, hook_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM hooks WHERE id=?", (hook_id,))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------- schedules
    def create_schedule(self, data: dict[str, Any]) -> dict:
        schedule_id = data.get("id") or f"sch_{os.urandom(5).hex()}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO schedules (id, name, kind, every_seconds, at_hour, at_minute, goal, "
                "workflow_id, project_id, enabled, next_run_at, created_at, owner_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    schedule_id, data["name"], data.get("kind", "interval"),
                    int(data.get("every_seconds") or 3600), data.get("at_hour"),
                    data.get("at_minute"), data.get("goal", ""), data.get("workflow_id"),
                    data.get("project_id"), int(data.get("enabled", 1)),
                    data.get("next_run_at"), data["created_at"], data.get("owner_id"),
                ),
            )
            self._conn.commit()
        return self.get_schedule(schedule_id) or {}

    def get_schedule(self, schedule_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        return dict(row) if row else None

    def list_schedules(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM schedules ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def due_schedules(self, now: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM schedules WHERE enabled=1 AND next_run_at IS NOT NULL "
                "AND next_run_at <= ? ORDER BY next_run_at LIMIT ?", (now, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def update_schedule_run(self, schedule_id: str, last_run_at: str, next_run_at: str,
                            last_run_id: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE schedules SET last_run_at=?, next_run_at=?, last_run_id=? WHERE id=?",
                (last_run_at, next_run_at, last_run_id, schedule_id),
            )
            self._conn.commit()

    def set_schedule_enabled(self, schedule_id: str, enabled: bool) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE schedules SET enabled=? WHERE id=?", (int(enabled), schedule_id)
            )
            self._conn.commit()
            return cur.rowcount

    def delete_schedule(self, schedule_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
            self._conn.commit()
            return cur.rowcount

    # --------------------------------------------------------- notifications
    def add_notification(self, run_id: str | None, kind: str, target: str | None, ok: bool,
                         attempts: int, error: str | None, ts: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO notifications (run_id, kind, target, ok, attempts, error, ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_id, kind, target, int(ok), attempts, error, ts),
            )
            self._conn.commit()

    def list_notifications(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- leases
    def claim_next_run(self, worker_id: str, lease_seconds: int, now: str,
                       expires: str) -> dict | None:
        """Atomically take one queued run. Safe with several workers on one database."""
        with self._lock:
            row = self._conn.execute(
                """UPDATE runs
                   SET status='running', lease_owner=?, lease_expires_at=?, updated_at=?
                   WHERE id = (
                       SELECT id FROM runs
                       WHERE status='queued'
                         AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                       ORDER BY priority DESC, created_at ASC
                       LIMIT 1
                   )
                   RETURNING id""",
                (worker_id, expires, now, now),
            ).fetchone()
            self._conn.commit()
        if not row:
            return None
        return self.get_run(row["id"])

    def release_lease(self, run_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET lease_owner=NULL, lease_expires_at=NULL WHERE id=?", (run_id,)
            )
            self._conn.commit()
            return cur.rowcount

    def requeue_expired(self, now: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status='queued', lease_owner=NULL, lease_expires_at=NULL "
                "WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (now,),
            )
            self._conn.commit()
            return cur.rowcount

    def queued_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM runs WHERE status='queued'"
            ).fetchone()
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------- projects
    def create_project(self, name: str, ts: str, meta: dict | None = None) -> dict[str, Any]:
        project_id = f"proj_{os.urandom(5).hex()}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects (id, name, created_at, meta) VALUES (?,?,?,?)",
                (project_id, name, ts, json.dumps(meta or {})),
            )
            self._conn.commit()
        return {"id": project_id, "name": name, "created_at": ts, "meta": meta or {}}

    def list_projects(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["meta"] = json.loads(data.get("meta") or "{}")
            data["runs"] = self._conn.execute(
                "SELECT COUNT(*) AS n FROM runs WHERE project_id=?", (row["id"],)
            ).fetchone()["n"]
            out.append(data)
        return out

    def add_project_member(self, project_id: str, user_id: str, role: str, ts: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO project_members (project_id, user_id, role, created_at) "
                "VALUES (?,?,?,?) ON CONFLICT(project_id, user_id) DO UPDATE SET role=excluded.role",
                (project_id, user_id, role, ts),
            )
            self._conn.commit()

    def list_project_members(self, project_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.user_id, m.role, m.created_at, u.email FROM project_members m "
                "LEFT JOIN users u ON u.id = m.user_id WHERE m.project_id=? ORDER BY m.created_at",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def project_role(self, project_id: str, user_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT role FROM project_members WHERE project_id=? AND user_id=?",
                (project_id, user_id),
            ).fetchone()
        return row["role"] if row else None

    def remove_project_member(self, project_id: str, user_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM project_members WHERE project_id=? AND user_id=?", (project_id, user_id)
            )
            self._conn.commit()
            return cur.rowcount

    def set_project_quota(self, project_id: str, tokens: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE projects SET token_quota=? WHERE id=?", (int(tokens), project_id)
            )
            self._conn.commit()
            return cur.rowcount

    def project_usage_tokens(self, project_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(r.tokens), 0) AS n FROM runs r WHERE r.project_id=?",
                (project_id,),
            ).fetchone()
        return int(row["n"])

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["meta"] = json.loads(data.get("meta") or "{}")
        data["runs"] = self.list_runs(limit=100, project_id=project_id)
        data["members"] = self.list_project_members(project_id)
        data["tokens_used"] = self.project_usage_tokens(project_id)
        return data

    # ----------------------------------------------------------- tool calls
    def add_tool_call(self, record: dict[str, Any], ts: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO tool_calls (run_id, agent_id, tool, ok, duration_s, error, args_summary, ts)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    record.get("run_id"), record.get("agent_id"), record.get("tool"),
                    int(bool(record.get("ok"))), float(record.get("duration_s") or 0.0),
                    record.get("error") or None, record.get("args_summary"), ts,
                ),
            )
            self._conn.commit()

    def tool_calls(self, run_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tool_calls"
        args: list[Any] = []
        if run_id:
            sql += " WHERE run_id=?"
            args.append(run_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------- kb
    def add_document(self, run_id: str | None, title: str, text: str, ts: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO documents (run_id, title, text, ts) VALUES (?,?,?,?)",
                (run_id, title, text, ts),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def documents(self, limit: int = 5000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, run_id, title, text, ts FROM documents ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
