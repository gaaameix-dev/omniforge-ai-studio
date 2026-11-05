"""PostgreSQL-backed RunStore.

The store talks SQLite through `self._conn.execute(...)`, so this module supplies a
thin DB-API shim with the same shape: `?` placeholders are translated, rows come
back as dicts, and `lastrowid` is filled from `RETURNING id`. Everything above the
connection - orchestrator, API, tests - is unchanged.

Verified in the suite against a real PostgreSQL 16 server.
"""
from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from . import pg
from .store import RunStore

try:  # optional dependency
    import psycopg
    from psycopg.rows import dict_row

    PSYCOPG_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the driver
    PSYCOPG_AVAILABLE = False


class PgCursor:
    def __init__(self, cursor, lastrowid: Any = None) -> None:
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        row = self._cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self) -> list[dict]:
        return [dict(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        for row in self._cursor:
            yield dict(row)

    def fetchmany(self, size: int = 1) -> list[dict]:
        return [dict(row) for row in self._cursor.fetchmany(size)]

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def close(self) -> None:
        self._cursor.close()


class PgConnection:
    """Minimal sqlite3-compatible surface over psycopg."""

    def __init__(self, url: str) -> None:
        if not PSYCOPG_AVAILABLE:
            raise pg.PostgresUnavailable(
                "PostgreSQL support needs psycopg: pip install 'psycopg[binary]'"
            )
        try:
            self._conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        except Exception as exc:  # noqa: BLE001 - surface a clear, actionable message
            raise pg.PostgresUnavailable(f"cannot connect to PostgreSQL: {exc}") from None

    # ------------------------------------------------------------------ execute
    @staticmethod
    def _translate(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> PgCursor:
        statement = self._translate(sql)
        upper = statement.upper()
        wants_id = upper.lstrip().startswith("INSERT") and "RETURNING" not in upper
        if wants_id:
            statement = statement.rstrip().rstrip(";") + " RETURNING id"
        cursor = self._conn.cursor()
        cursor.execute(statement, tuple(params or ()))
        lastrowid = None
        if wants_id:
            try:
                row = cursor.fetchone()
                lastrowid = (row or {}).get("id")
            except Exception:  # noqa: BLE001 - table without an id column
                lastrowid = None
        return PgCursor(cursor, lastrowid)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> PgCursor:
        cursor = self._conn.cursor()
        cursor.executemany(self._translate(sql), seq)
        return PgCursor(cursor)

    def executescript(self, script: str) -> None:
        cursor = self._conn.cursor()
        for statement in [s.strip() for s in script.split(";") if s.strip()]:
            cursor.execute(statement)

    def commit(self) -> None:  # autocommit
        return None

    def close(self) -> None:
        self._conn.close()

    @property
    def raw(self):
        return self._conn


class PostgresStore(RunStore):
    """Same API as RunStore, backed by PostgreSQL."""

    def __init__(self, url: str, *, create_schema: bool = True, schema: str = "") -> None:
        self.url = url
        self.schema = schema
        self.path = Path("postgres://…")
        self._lock = threading.RLock()
        self._conn = PgConnection(url)
        if schema:
            with self._conn.raw.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}", public')
        if create_schema:
            pg.init_schema(self._conn.raw)
        self._runs_dir = Path(".")
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    # the SQLite initialiser is replaced wholesale
    def _migrate(self) -> None:
        return None

    def backend(self) -> str:
        return "postgres"

    def close(self) -> None:
        with self._lock:
            self._conn.close()
