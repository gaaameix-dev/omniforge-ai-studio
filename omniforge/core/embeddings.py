"""Optional embedding-backed retrieval.

BM25 stays the default because it needs no provider and no extra latency. When an
embedding key is configured, documents get vectors on write and `/api/knowledge/search`
can answer semantically; the hybrid scorer blends both so exact names still win.
"""
from __future__ import annotations

import json
import math
from typing import Any

import httpx

EMBED_MODEL = "text-embedding-3-small"


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider:
    """OpenAI-compatible /embeddings client (works with Azure, vLLM, Ollama too)."""

    def __init__(self, api_key: str, *, model: str = EMBED_MODEL,
                 base_url: str = "https://api.openai.com/v1", timeout: float = 30.0,
                 transport: Any | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        payload = {"model": self.model, "input": texts}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.post(f"{self.base_url}/embeddings", json=payload, headers=headers)
            if resp.status_code >= 400:
                raise EmbeddingError(f"embeddings endpoint returned {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
        rows = data.get("data") or []
        if len(rows) != len(texts):
            raise EmbeddingError("embeddings response did not match the request size")
        return [row["embedding"] for row in rows]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


class VectorIndex:
    """Vectors live next to the documents in SQLite; no extra service needed."""

    def __init__(self, store) -> None:
        self.store = store

    def put(self, document_id: int, vector: list[float]) -> None:
        self.store.put_vector(document_id, json.dumps(vector))

    def get(self, document_id: int) -> list[float] | None:
        raw = self.store.get_vector(document_id)
        return json.loads(raw) if raw else None

    def search(self, query_vector: list[float], limit: int = 5) -> list[tuple[int, float]]:
        scored = []
        for row in self.store.all_vectors():
            vector = json.loads(row["vector"])
            scored.append((row["document_id"], cosine(query_vector, vector)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[: max(0, limit)]


async def embed_documents(knowledge, provider: EmbeddingProvider, documents: list[dict]) -> int:
    """Vectorise documents that do not have a vector yet. Returns how many were added."""
    if not provider.enabled:
        return 0
    index = VectorIndex(knowledge.store)
    pending = [d for d in documents if index.get(d["id"]) is None]
    if not pending:
        return 0
    vectors = await provider.embed([f"{d['title']}\n{d['text'][:4000]}" for d in pending])
    for document, vector in zip(pending, vectors, strict=False):
        index.put(document["id"], vector)
    return len(pending)
