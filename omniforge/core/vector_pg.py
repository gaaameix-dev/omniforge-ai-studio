"""pgvector-backed retrieval with an HNSW index.

SQLite cosine (the default) is fine to a few thousand documents. Past that, an ANN
index matters, so this stores embeddings in PostgreSQL with pgvector and searches
with `<=>` (cosine distance). It is only used when the backend is PostgreSQL, the
extension exists and `OMNIFORGE_VECTOR_BACKEND=pgvector`.
"""
from __future__ import annotations

import json
from typing import Any

from . import pg

TABLE = "doc_embeddings"


class VectorError(RuntimeError):
    pass


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


def available(url: str) -> dict[str, Any]:
    """Report whether pgvector can be used without creating anything permanent."""
    try:
        conn = pg.connect(url)
    except pg.PostgresUnavailable as exc:
        return {"available": False, "reason": str(exc)}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = cur.fetchone()
            if row:
                return {"available": True, "extension": row[0], "installed": True}
            cur.execute("SELECT name FROM pg_available_extensions WHERE name = 'vector'")
            row = cur.fetchone()
            if row:
                return {"available": True, "installed": False,
                        "hint": "run `CREATE EXTENSION vector` (the index does it for you)"}
            return {"available": False, "reason": "the vector extension is not installed"}
    finally:
        conn.close()


class PgVectorIndex:
    def __init__(self, url: str, dim: int = 1536, *, table: str = TABLE,
                 schema: str = "public") -> None:
        self.url = url
        self.dim = int(dim)
        self.table = table
        self.schema = schema

    def _qualified(self) -> str:
        return f'"{self.schema}"."{self.table}"'

    def ensure_schema(self) -> dict[str, Any]:
        conn = pg.connect(self.url)
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._qualified()} ("
                    "document_id BIGINT PRIMARY KEY, "
                    f"embedding vector({self.dim}) NOT NULL, "
                    "metadata TEXT NOT NULL DEFAULT '{}')"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.table}_hnsw ON {self._qualified()} "
                    "USING hnsw (embedding vector_cosine_ops)"
                )
            return {"table": self._qualified(), "dim": self.dim, "index": "hnsw"}
        finally:
            conn.close()

    def put(self, document_id: int, vector: list[float],
            metadata: dict[str, Any] | None = None) -> None:
        if len(vector) != self.dim:
            raise VectorError(f"expected a {self.dim}-dimension vector, got {len(vector)}")
        conn = pg.connect(self.url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self._qualified()} (document_id, embedding, metadata) "
                    "VALUES (%s, %s::vector, %s) ON CONFLICT (document_id) DO UPDATE "
                    "SET embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata",
                    (document_id, _literal(vector), json.dumps(metadata or {})),
                )
        finally:
            conn.close()

    def put_many(self, rows: list[tuple[int, list[float]]]) -> int:
        for document_id, vector in rows:
            self.put(document_id, vector)
        return len(rows)

    def search(self, vector: list[float], limit: int = 5) -> list[dict[str, Any]]:
        if len(vector) != self.dim:
            raise VectorError(f"expected a {self.dim}-dimension vector, got {len(vector)}")
        conn = pg.connect(self.url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT document_id, metadata, 1 - (embedding <=> %s::vector) AS score "
                    f"FROM {self._qualified()} ORDER BY embedding <=> %s::vector LIMIT %s",
                    (_literal(vector), _literal(vector), int(limit)),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [{"document_id": row[0], "metadata": json.loads(row[1] or "{}"),
                 "score": round(float(row[2]), 4)} for row in rows]

    def count(self) -> int:
        conn = pg.connect(self.url)
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {self._qualified()}")
                return int(cur.fetchone()[0])
        finally:
            conn.close()

    def delete(self, document_id: int) -> int:
        conn = pg.connect(self.url)
        try:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self._qualified()} WHERE document_id = %s",
                            (document_id,))
                return cur.rowcount
        finally:
            conn.close()
