"""Semantic retrieval, plugin install over HTTPS, Postgres seam, legal docs."""
from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest
from conftest import make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core import pg
from omniforge.core.embeddings import EmbeddingError, EmbeddingProvider, VectorIndex, cosine
from omniforge.core.knowledge import KnowledgeBase
from omniforge.core.plugins import PluginError, install_from_url
from omniforge.core.store import RunStore

PASSWORD = "hunter2hunter2"


def _vector_handler(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        vectors = []
        for text in body["input"]:
            lowered = text.lower()
            if "postgres" in lowered or "database" in lowered or "vacuum" in lowered:
                vectors.append([1.0, 0.0, 0.0])
            elif "audio" in lowered or "mix" in lowered:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return httpx.Response(200, json={"data": [{"embedding": v} for v in vectors]})
    return httpx.MockTransport(handler)


# --------------------------------------------------------------- embeddings
def test_cosine_similarity():
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([], [1]) == 0.0
    assert cosine([1, 2], [1, 2, 3]) == 0.0


async def test_embedding_provider_shapes_request():
    seen: list = []
    provider = EmbeddingProvider("k", transport=_vector_handler(seen))
    vectors = await provider.embed(["postgres schema", "audio mix"])
    assert vectors[0] == [1.0, 0.0, 0.0] and vectors[1] == [0.0, 1.0, 0.0]
    assert seen[0]["model"] == "text-embedding-3-small"
    assert await provider.embed([]) == []


async def test_embedding_errors_are_explicit():
    transport = httpx.MockTransport(lambda r: httpx.Response(429, text="slow down"))
    provider = EmbeddingProvider("k", transport=transport)
    with pytest.raises(EmbeddingError):
        await provider.embed(["x"])


def test_vector_index_roundtrip(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    index = VectorIndex(store)
    index.put(1, [1.0, 2.0])
    assert index.get(1) == [1.0, 2.0]
    index.put(1, [3.0])
    assert index.get(1) == [3.0]
    assert index.get(99) is None
    store.close()


async def test_hybrid_search_prefers_semantic_match(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    kb = KnowledgeBase(store)
    kb.add("r1", "postgres schema", "indexes and vacuum", "t")
    kb.add("r2", "audio mix", "compressor settings", "t")
    provider = EmbeddingProvider("k", transport=_vector_handler([]))

    from omniforge.core.embeddings import embed_documents

    assert await embed_documents(kb, provider, store.documents()) == 2
    query_vector = (await provider.embed(["database performance"]))[0]
    hits = kb.search_hybrid("vacuum", query_vector, limit=2)
    assert hits[0]["title"] == "postgres schema"
    assert hits[0]["semantic"] == 1.0
    # without a vector the hybrid search degrades to lexical
    lexical_only = kb.search_hybrid("compressor", None, limit=2)
    assert lexical_only[0]["title"] == "audio mix"
    store.close()


def test_semantic_search_endpoint_needs_a_key(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/api/knowledge/search", params={"q": "x", "mode": "semantic"})
        assert response.status_code == 409
        lexical = client.get("/api/knowledge/search", params={"q": "x"})
        assert lexical.status_code == 200 and lexical.json()["mode"] == "lexical"


# ------------------------------------------------------------- plugin by URL
def _plugin_bytes(manifest: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
    return buffer.getvalue()


MANIFEST = {"name": "remote-pack", "version": "2.0.0", "permissions": ["net.read"],
            "skills": [{"name": "remote_skill", "department": "research",
                        "description": "remote"}]}


async def test_install_plugin_from_https_url(tmp_path):
    payload = _plugin_bytes(MANIFEST)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=payload))
    store = RunStore(tmp_path / "db.sqlite")
    record = await install_from_url(store, tmp_path, "https://plugins.example.com/p.zip",
                                    make_settings(tmp_path), None, "2026-01-01T00:00:00+00:00",
                                    transport=transport)
    assert record["name"] == "remote-pack" and record["source_url"].startswith("https://")
    assert store.list_plugins()[0]["version"] == "2.0.0"
    store.close()


async def test_plugin_url_rules(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    settings = make_settings(tmp_path)
    with pytest.raises(PluginError):
        await install_from_url(store, tmp_path, "http://insecure.example.com/p.zip", settings,
                               None, "t")
    failing = httpx.MockTransport(lambda r: httpx.Response(404))
    with pytest.raises(PluginError):
        await install_from_url(store, tmp_path, "https://x.example.com/p.zip", settings, None, "t",
                               transport=failing)
    big = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 2048))
    with pytest.raises(PluginError):
        await install_from_url(store, tmp_path, "https://x.example.com/p.zip", settings, None, "t",
                               transport=big, max_bytes=1024)
    store.close()


async def test_install_from_url_endpoint(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True,
                                             scheduler_enabled=False))) as client:
        body = client.post("/api/auth/register",
                           json={"email": "owner@example.com", "password": PASSWORD}).json()
        headers = {"Authorization": f"Bearer {body['token']}"}
        # no network in tests: an unreachable host must fail cleanly, not hang
        response = client.post("/api/plugins/install-url",
                               json={"url": "https://127.0.0.1:9/none.zip"}, headers=headers)
        assert response.status_code in (400, 502)


# ---------------------------------------------------------------- Postgres
def test_postgres_schema_is_complete():
    statements = pg.schema_statements()
    joined = " ".join(statements)
    for table in ("runs", "tasks", "events", "documents"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in joined
    assert "idx_tasks_run" in joined


def test_store_from_url_sqlite(tmp_path):
    store = pg.store_from_url(f"sqlite:///{tmp_path / 'x.db'}")
    assert store.stats()["runs"] == 0
    store.close()


def test_postgres_url_either_connects_or_explains_itself():
    """With the driver a Postgres URL builds a store; without it, a clear error."""
    try:
        store = pg.store_from_url("postgres://user:pw@127.0.0.1:1/db?connect_timeout=1")
    except pg.PostgresUnavailable as exc:
        assert "psycopg" in str(exc) or "PostgreSQL" in str(exc)
    else:
        assert store.backend() == "postgres"
        store.close()


def test_unknown_database_url_is_rejected():
    with pytest.raises(ValueError):
        pg.store_from_url("mysql://nope")


def test_db_status_endpoint(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        info = client.get("/api/db").json()
        assert info["driver"] == "sqlite" and info["ok"] is True
        assert info["active"] == "sqlite"


# ------------------------------------------------------------------- docs
def test_legal_templates_exist():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "docs"
    privacy = (root / "PRIVACY.md").read_text()
    terms = (root / "TERMS.md").read_text()
    assert "template" in privacy.lower() and "What leaves the machine" in privacy
    assert "template" in terms.lower() and "Acceptable use" in terms
