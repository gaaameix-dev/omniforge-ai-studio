"""Schema-per-tenant for PostgreSQL.

Each tenant gets its own schema with the same tables; the connection sets
`search_path` to it. SQLite deployments keep the shared-schema behaviour and the
`workspace_id` column instead.
"""
from __future__ import annotations

import re
from typing import Any

from . import pg

SAFE_SCHEMA = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


class TenantError(ValueError):
    pass


def schema_name(tenant: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", (tenant or "").lower()).strip("_")
    if not cleaned or not SAFE_SCHEMA.match(cleaned):
        raise TenantError(f"invalid tenant name '{tenant}'")
    return f"tenant_{cleaned}"


def create_tenant(url: str, tenant: str) -> dict[str, Any]:
    """Create the tenant schema and apply the full table set inside it."""
    schema = schema_name(tenant)
    conn = pg.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            for statement in pg.schema_statements():
                cur.execute(statement)
            cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema = %s", (schema,))
            tables = cur.fetchone()[0]
        return {"tenant": tenant, "schema": schema, "tables": tables}
    finally:
        conn.close()


def list_tenants(url: str) -> list[dict[str, Any]]:
    conn = pg.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name LIKE 'tenant_%' ORDER BY schema_name")
            return [{"schema": row[0]} for row in cur.fetchall()]
    finally:
        conn.close()


def drop_tenant(url: str, tenant: str) -> dict[str, Any]:
    schema = schema_name(tenant)
    conn = pg.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        return {"tenant": tenant, "schema": schema, "dropped": True}
    finally:
        conn.close()
