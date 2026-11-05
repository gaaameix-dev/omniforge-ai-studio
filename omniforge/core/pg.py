"""PostgreSQL seam.

The store is one class with one connection, so swapping the backend is contained.
This module ships the PostgreSQL schema and a thin adapter; if the driver is not
installed it fails with a clear message instead of pretending to work.

Status: the DDL and the factory are tested; a live PostgreSQL server is not
available in this environment, so the adapter itself is UNVERIFIED against a real
database. Treat it as a starting point, not a production claim.
"""
from __future__ import annotations

import re
from pathlib import Path


def sqlite_schema_to_pg(sql: str) -> str:
    """Translate the SQLite DDL so both backends share one schema definition."""
    out = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    out = re.sub(r"\bREAL\b", "DOUBLE PRECISION", out)
    return out


def schema_statements() -> list[str]:
    from .store import SCHEMA  # single source of truth, imported lazily

    return [stmt.strip() for stmt in sqlite_schema_to_pg(SCHEMA).split(";") if stmt.strip()]


SCHEMA_PG = ""  # kept for import compatibility; use schema_statements()


class PostgresUnavailable(RuntimeError):
    pass


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
    """`sqlite:///path.db` -> RunStore; `postgresql://…` -> PostgresStore."""
    from .store import RunStore
    from .store_pg import PostgresStore

    if url.startswith("sqlite:///"):
        return RunStore(Path(url[len("sqlite:///"):]))
    if url.startswith("sqlite://"):
        return RunStore(Path(url[len("sqlite://"):]))
    if url.startswith(("postgres://", "postgresql://")):
        return PostgresStore(url)
    raise ValueError(f"unsupported database URL: {url}")
