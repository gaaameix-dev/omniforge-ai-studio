"""Cross-run knowledge base with BM25 retrieval (no embedding service needed).

Every finished run indexes its summary and task results. Later runs search that
corpus, so the framework accumulates a memory of what it already produced
instead of starting cold each time. Swap in a vector store by replacing
`search` - nothing else calls the index directly.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "are", "was", "were",
    "will", "have", "has", "had", "not", "but", "you", "your", "our", "its", "all",
    "any", "can", "may", "use", "used", "using", "each", "one", "two", "how", "what",
    "when", "which", "who", "why", "them", "they", "there", "here", "then", "than",
    "also", "such", "more", "most", "some", "only", "own", "out", "off", "per",
}


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out: list[str] = []
    for w in words:
        if len(w) < 3 or w in STOPWORDS:
            continue
        if w.endswith("ing") and len(w) > 5:
            w = w[:-3]
        elif w.endswith("ed") and len(w) > 4:
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 4:
            w = w[:-1]
        # "runn" -> "run", "shipp" -> "ship"
        if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouls":
            w = w[:-1]
        out.append(w)
    return out


@dataclass
class Doc:
    id: int
    run_id: str | None
    title: str
    text: str
    ts: str


class KnowledgeBase:
    def __init__(self, store, k1: float = 1.5, b: float = 0.75) -> None:
        self.store = store
        self.k1 = k1
        self.b = b
        self._docs: list[Doc] = []
        self._postings: dict[str, dict[int, int]] = {}
        self._lengths: list[int] = []
        self._avg_len: float = 0.0
        self._dirty = True

    # ---------------------------------------------------------------- indexing
    def add(self, run_id: str | None, title: str, text: str, ts: str) -> int:
        doc_id = self.store.add_document(run_id, title, text, ts)
        self._dirty = True
        return doc_id

    def _ensure(self) -> None:
        if not self._dirty:
            return
        rows = self.store.documents()
        self._docs = [Doc(r["id"], r["run_id"], r["title"], r["text"], r["ts"]) for r in rows]
        self._postings = {}
        self._lengths = []
        for idx, doc in enumerate(self._docs):
            tokens = tokenize(doc.title + " " + doc.text)
            self._lengths.append(len(tokens))
            for term, count in Counter(tokens).items():
                self._postings.setdefault(term, {})[idx] = count
        self._avg_len = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        self._dirty = False

    # ---------------------------------------------------------------- searching
    def search(
        self, query: str, limit: int = 5, min_score: float = 0.0, exclude_run: str | None = None
    ) -> list[dict[str, Any]]:
        self._ensure()
        if not self._docs:
            return []
        terms = tokenize(query)
        if not terms:
            return []
        n = len(self._docs)
        scores: dict[int, float] = {}
        for term in terms:
            postings = self._postings.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for idx, tf in postings.items():
                dl = self._lengths[idx] or 1
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self._avg_len or 1))
                scores[idx] = scores.get(idx, 0.0) + idf * (tf * (self.k1 + 1)) / denom
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        out: list[dict[str, Any]] = []
        for idx, score in ranked:
            if len(out) >= max(0, limit):
                break
            if score < min_score:
                continue
            doc = self._docs[idx]
            if exclude_run and doc.run_id == exclude_run:
                continue
            out.append({
                "id": doc.id,
                "run_id": doc.run_id,
                "title": doc.title,
                "snippet": doc.text[:400],
                "score": round(score, 4),
                "ts": doc.ts,
            })
        return out

    def context_for(
        self, query: str, limit: int = 3, max_chars: int = 1500, exclude_run: str | None = None
    ) -> str:
        hits = self.search(query, limit=limit, exclude_run=exclude_run)
        if not hits:
            return ""
        parts: list[str] = []
        used = 0
        for hit in hits:
            chunk = f"- ({hit['score']:.2f}) {hit['title']}: {hit['snippet']}"
            if used + len(chunk) > max_chars:
                break
            parts.append(chunk)
            used += len(chunk)
        return "\n".join(parts)

    def search_hybrid(self, query: str, query_vector: list[float] | None, limit: int = 5,
                      exclude_run: str | None = None, alpha: float = 0.6) -> list[dict[str, Any]]:
        """Blend BM25 with cosine similarity; falls back to BM25 without vectors."""
        lexical = {hit["id"]: hit for hit in self.search(query, limit=limit * 3,
                                                         exclude_run=exclude_run)}
        if not query_vector:
            return list(lexical.values())[:limit]
        from .embeddings import cosine

        semantic: dict[int, float] = {}
        for row in self.store.all_vectors():
            semantic[row["document_id"]] = cosine(query_vector, json.loads(row["vector"]))
        ids = set(lexical) | {doc_id for doc_id, score in semantic.items() if score > 0.2}
        documents = {d["id"]: d for d in self.store.documents_by_ids(sorted(ids))}
        top_lexical = max((h["score"] for h in lexical.values()), default=1.0) or 1.0
        ranked: list[dict[str, Any]] = []
        for doc_id in ids:
            doc = documents.get(doc_id)
            if doc is None or (exclude_run and doc["run_id"] == exclude_run):
                continue
            lex = lexical.get(doc_id, {}).get("score", 0.0) / top_lexical
            sem = semantic.get(doc_id, 0.0)
            ranked.append({
                "id": doc_id, "run_id": doc["run_id"], "title": doc["title"],
                "snippet": doc["text"][:400], "ts": doc["ts"],
                "score": round(alpha * sem + (1 - alpha) * lex, 4),
                "semantic": round(sem, 4), "lexical": round(lex, 4),
            })
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:limit]

    def stats(self) -> dict[str, Any]:
        self._ensure()
        return {
            "documents": len(self._docs),
            "terms": len(self._postings),
            "avg_doc_length": round(self._avg_len, 1),
        }
