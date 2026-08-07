"""Semantic health-knowledge retrieval.

Primary path: Qdrant (local on-disk or a remote server) with MiniLM sentence
embeddings from `fastembed`, which runs the ONNX model without pulling in
torch.

Fallback path: an in-process TF-IDF cosine index. It activates when the
embedding model cannot be downloaded (offline machine, restricted network) so
retrieval, and therefore grounded explanation, keeps working in a demo.

Patient-specific data is never indexed here — only the general corpus.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass

from app.core.config import settings
from app.knowledge.corpus import CORPUS, KnowledgeDoc

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "for", "with", "at", "by", "from", "as", "that",
    "this", "it", "its", "can", "may", "should", "would", "which", "when",
    "what", "how", "if", "not", "no", "do", "does", "did", "have", "has",
    "had", "i", "you", "my", "your", "me", "we", "they", "them", "their",
    "there", "than", "then", "so", "but", "about", "into", "over", "more",
}


@dataclass
class RetrievedDoc:
    doc: KnowledgeDoc
    score: float

    def to_citation(self) -> dict:
        return {
            "id": self.doc.id,
            "title": self.doc.title,
            "topic": self.doc.topic,
            "source": self.doc.source,
            "score": round(self.score, 4),
        }


def _tokenise(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9']+", (text or "").lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 2]


class _TfidfIndex:
    """Small dependency-free cosine index over the corpus."""

    def __init__(self, docs: list[KnowledgeDoc]) -> None:
        self.docs = docs
        self._doc_terms: list[Counter] = []
        df: Counter = Counter()

        for doc in docs:
            terms = Counter(_tokenise(f"{doc.title} {doc.topic} {doc.text}"))
            self._doc_terms.append(terms)
            df.update(terms.keys())

        total = len(docs) or 1
        self._idf = {
            term: math.log((total + 1) / (count + 1)) + 1.0 for term, count in df.items()
        }
        self._doc_vectors = [self._vectorise(terms) for terms in self._doc_terms]

    def _vectorise(self, terms: Counter) -> dict[str, float]:
        vector = {
            term: (1 + math.log(count)) * self._idf.get(term, 1.0)
            for term, count in terms.items()
        }
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
        return {term: value / norm for term, value in vector.items()}

    def search(self, query: str, limit: int) -> list[RetrievedDoc]:
        query_vector = self._vectorise(Counter(_tokenise(query)))
        if not query_vector:
            return []

        scored: list[tuple[float, int]] = []
        for index, doc_vector in enumerate(self._doc_vectors):
            # Cosine similarity; both vectors are already L2-normalised.
            score = sum(
                weight * doc_vector.get(term, 0.0) for term, weight in query_vector.items()
            )
            if score > 0:
                scored.append((score, index))

        scored.sort(reverse=True)
        return [RetrievedDoc(self.docs[i], score) for score, i in scored[:limit]]


class KnowledgeService:
    """Lazily initialised retrieval service with a graceful fallback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False
        self._backend = "uninitialised"
        self._client = None
        self._embedder = None
        self._tfidf: _TfidfIndex | None = None

    # -- lifecycle ---------------------------------------------------------
    def ensure_ready(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            try:
                self._init_qdrant()
                self._backend = "qdrant+minilm"
            except Exception as exc:  # noqa: BLE001 - any failure must degrade
                logger.warning(
                    "Qdrant/MiniLM retrieval unavailable (%s); "
                    "falling back to the in-process TF-IDF index.",
                    exc,
                )
                self._tfidf = _TfidfIndex(CORPUS)
                self._backend = "tfidf-fallback"
            self._ready = True

    def _init_qdrant(self) -> None:
        from fastembed import TextEmbedding
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, PointStruct, VectorParams

        self._embedder = TextEmbedding(model_name=settings.embedding_model)

        if settings.qdrant_url:
            self._client = QdrantClient(url=settings.qdrant_url)
        else:
            # Local on-disk mode: real Qdrant semantics, no server to run.
            self._client = QdrantClient(path=str(settings.qdrant_path))

        collection = settings.qdrant_collection
        existing = {c.name for c in self._client.get_collections().collections}

        vectors = list(self._embedder.embed([doc.text for doc in CORPUS]))
        dimension = len(vectors[0])

        if collection not in existing:
            self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
            )

        # Upsert is idempotent, so a restart re-syncs the corpus cheaply.
        self._client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=index,
                    vector=vector.tolist(),
                    payload={
                        "doc_id": doc.id,
                        "title": doc.title,
                        "topic": doc.topic,
                        "audience": doc.audience,
                        "source": doc.source,
                        "text": doc.text,
                    },
                )
                for index, (doc, vector) in enumerate(zip(CORPUS, vectors))
            ],
        )

    # -- query -------------------------------------------------------------
    def search(
        self, query: str, *, limit: int = 4, topic: str | None = None
    ) -> list[RetrievedDoc]:
        if not (query or "").strip():
            return []
        self.ensure_ready()

        if self._backend.startswith("qdrant"):
            try:
                return self._search_qdrant(query, limit=limit, topic=topic)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Qdrant query failed (%s); using TF-IDF fallback.", exc)
                if self._tfidf is None:
                    self._tfidf = _TfidfIndex(CORPUS)

        index = self._tfidf or _TfidfIndex(CORPUS)
        results = index.search(query, limit * 3 if topic else limit)
        if topic:
            results = [r for r in results if r.doc.topic == topic][:limit]
        return results[:limit]

    def _search_qdrant(
        self, query: str, *, limit: int, topic: str | None
    ) -> list[RetrievedDoc]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        from app.knowledge.corpus import CORPUS_BY_ID

        vector = list(self._embedder.embed([query]))[0].tolist()
        query_filter = (
            Filter(must=[FieldCondition(key="topic", match=MatchValue(value=topic))])
            if topic
            else None
        )
        hits = self._client.search(
            collection_name=settings.qdrant_collection,
            query_vector=vector,
            query_filter=query_filter,
            limit=limit,
        )
        out: list[RetrievedDoc] = []
        for hit in hits:
            doc = CORPUS_BY_ID.get((hit.payload or {}).get("doc_id", ""))
            if doc:
                out.append(RetrievedDoc(doc, float(hit.score)))
        return out

    def build_context(self, query: str, *, limit: int = 3) -> tuple[str, list[dict]]:
        """Return (grounding text, citations) for injection into an LLM prompt."""
        results = self.search(query, limit=limit)
        if not results:
            return "", []
        blocks = [f"[{r.doc.id}] {r.doc.title}\n{r.doc.text}" for r in results]
        return "\n\n".join(blocks), [r.to_citation() for r in results]

    @property
    def backend(self) -> str:
        self.ensure_ready()
        return self._backend


knowledge_service = KnowledgeService()
