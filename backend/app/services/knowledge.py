"""Semantic retrieval over several collections.

Primary path: Qdrant (local on-disk or a remote server) with MiniLM sentence
embeddings from `fastembed`, which runs the ONNX model without pulling in
torch.

Fallback path: an in-process TF-IDF cosine index. It activates when the
embedding model cannot be downloaded (offline machine, restricted network) so
retrieval, and therefore grounded explanation, keeps working in a demo.

Three collections, because they answer different questions and must not be
searched as one pool:

``clinical_knowledge``
    General patient guidance. Never patient-specific.

``provider_directory``
    Doctors, hospitals and diagnostic tests, **generated from the database**
    at ingest time — see ``app/knowledge/providers.py``. Add a doctor through
    the admin UI, re-ingest, and they become findable. Nothing about the
    directory is written by hand.

``policy_faq``
    How SuwaPath itself works: consent, private mode, what the AI does and
    does not do.

Mixing them would let "which doctor treats asthma?" retrieve an article
*about* asthma and answer with no doctor in it. Each route asks the collection
it actually needs.

Patient records are never indexed in any collection.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.knowledge.chunking import chunk_text
from app.knowledge.corpus import CORPUS, KnowledgeDoc

logger = logging.getLogger(__name__)

CLINICAL = "clinical_knowledge"
PROVIDERS = "provider_directory"
POLICY = "policy_faq"
COLLECTIONS = (CLINICAL, PROVIDERS, POLICY)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "for", "with", "at", "by", "from", "as", "that",
    "this", "it", "its", "can", "may", "should", "would", "which", "when",
    "what", "how", "if", "not", "no", "do", "does", "did", "have", "has",
    "had", "i", "you", "my", "your", "me", "we", "they", "them", "their",
    "there", "than", "then", "so", "but", "about", "into", "over", "more",
}


@dataclass(frozen=True)
class IndexedDoc:
    """One embedded passage, in any collection."""

    id: str
    title: str
    topic: str
    text: str
    source: str
    collection: str = CLINICAL
    audience: str = "patient"
    # Structured data the UI can render as a card. Not embedded.
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_knowledge(cls, doc: KnowledgeDoc, text: str, doc_id: str) -> "IndexedDoc":
        return cls(
            id=doc_id, title=doc.title, topic=doc.topic, text=text,
            source=doc.source, collection=CLINICAL, audience=doc.audience,
        )


@dataclass
class RetrievedDoc:
    doc: IndexedDoc
    score: float

    def to_citation(self) -> dict:
        return {
            "id": self.doc.id,
            "title": self.doc.title,
            "topic": self.doc.topic,
            "source": self.doc.source,
            "collection": self.doc.collection,
            "score": round(self.score, 4),
        }


# Irregular forms a suffix rule cannot reach. Kept deliberately short: this is
# a lookup for words where the patient's phrasing and the corpus's phrasing
# genuinely differ, not a general lemmatiser.
_LEMMA = {
    "burnt": "burn", "burned": "burn", "burns": "burn",
    "bitten": "bite", "bit": "bite", "bites": "bite",
    "fell": "fall", "fallen": "fall", "falls": "fall",
    "hurting": "pain", "hurts": "pain", "hurt": "pain", "aching": "pain",
    "ache": "pain", "aches": "pain", "sore": "pain", "painful": "pain",
    "bleeding": "bleed", "bled": "bleed",
    "swollen": "swelling", "swelled": "swelling",
    "children": "child", "teeth": "tooth", "feet": "foot",
    "vomited": "vomiting", "vomit": "vomiting",
    "breastfeed": "breastfeeding", "breastfed": "breastfeeding",
    "pregnant": "pregnancy", "diabetic": "diabetes",
}


def _stem(token: str) -> str:
    """Crude suffix stripping, so "headaches" matches "headache".

    Not a real stemmer. A real one is a dependency this project does not need
    for a corpus of this size, and the failure it is fixing is narrow: patients
    write "burnt my hand" and the corpus says "burn", so the lexical index
    scores zero on the one passage that answers them.
    """
    if token in _LEMMA:
        return _LEMMA[token]
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            stem = token[: -len(suffix)]
            return _LEMMA.get(stem, stem + "y" if suffix == "ies" else stem)
    return token


def _tokenise(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9']+", (text or "").lower())
    return [_stem(t) for t in tokens if t not in _STOPWORDS and len(t) > 2]


class _TfidfIndex:
    """Small dependency-free cosine index, used when embeddings are absent."""

    def __init__(self, docs: list[IndexedDoc]) -> None:
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

    def search(
        self, query: str, limit: int, *, collection: str | None = None
    ) -> list[RetrievedDoc]:
        query_vector = self._vectorise(Counter(_tokenise(query)))
        if not query_vector:
            return []

        scored: list[tuple[float, int]] = []
        for index, doc_vector in enumerate(self._doc_vectors):
            if collection and self.docs[index].collection != collection:
                continue
            # Cosine similarity; both vectors are already L2-normalised.
            score = sum(
                weight * doc_vector.get(term, 0.0) for term, weight in query_vector.items()
            )
            if score > 0:
                scored.append((score, index))

        scored.sort(reverse=True)
        return [RetrievedDoc(self.docs[i], score) for score, i in scored[:limit]]


# --------------------------------------------------------------------------
# Document sources
# --------------------------------------------------------------------------
_DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"


def _detect_local_server(url: str = _DEFAULT_QDRANT_URL, timeout: float = 0.4) -> str | None:
    """Use a Qdrant server on the usual port if one happens to be running.

    Embedded Qdrant takes an exclusive lock on its storage directory, so only
    one process can hold it. During development that is normally the API
    server, and every other process — the evaluation harness, a test script, a
    one-off query — silently drops to the TF-IDF index instead. That is how a
    retrieval bug survived: the fallback was what ran locally, and the fallback
    was the thing that was broken.

    Auto-detecting means `docker compose up -d qdrant` is the whole fix, with
    no configuration change and nothing to remember. An explicit `QDRANT_URL`
    still wins; this only fills the gap when nothing was configured.
    """
    try:
        import urllib.request

        with urllib.request.urlopen(f"{url}/healthz", timeout=timeout) as response:
            if response.status == 200:
                logger.info("Detected a Qdrant server at %s; using it.", url)
                return url
    except Exception:  # noqa: BLE001 — absence is the normal case, not an error
        return None
    return None


def clinical_documents() -> list[IndexedDoc]:
    """The curated corpus, chunked so each vector covers one idea."""
    docs: list[IndexedDoc] = []
    for entry in CORPUS:
        chunks = chunk_text(entry.text)
        for chunk in chunks:
            suffix = f"#{chunk.index}" if chunk.total > 1 else ""
            docs.append(IndexedDoc.from_knowledge(entry, chunk.text, f"{entry.id}{suffix}"))
    return docs


def provider_documents(db) -> list[IndexedDoc]:
    """The directory, regenerated from live rows. Empty if the DB is empty."""
    from app.knowledge.providers import build

    return [
        IndexedDoc(
            id=doc.id, title=doc.title, topic=doc.topic, text=doc.text,
            source=doc.source, collection=PROVIDERS, payload=doc.payload,
        )
        for doc in build(db)
    ]


def policy_documents() -> list[IndexedDoc]:
    from app.knowledge.policy import POLICY_DOCS

    return [
        IndexedDoc(
            id=doc["id"], title=doc["title"], topic=doc["topic"],
            text=doc["text"], source="SuwaPath platform documentation",
            collection=POLICY,
        )
        for doc in POLICY_DOCS
    ]


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------
class KnowledgeService:
    """Lazily initialised retrieval service with a graceful fallback."""

    # Vector search always returns its top-k, however poor the match. Asking
    # "who is Kusal Mendis" used to come back with the three least-unrelated
    # health articles in the corpus, which the model then dutifully wrote up.
    # Anything below this is treated as no match at all.
    #
    # There are two floors because there are two backends, and their scores
    # are not the same quantity. Embedding cosine similarity sits high even
    # for loose matches; TF-IDF cosine over a small corpus sits far lower —
    # a *correct* top hit for "fever and calf pain after paddy work" scores
    # 0.24. One floor of 0.35 for both meant that whenever Qdrant was
    # unavailable — which happens on any restart, and whenever another
    # process holds the embedded store — the entire knowledge base went
    # silently unreachable while still reporting itself healthy.
    #
    # The TF-IDF figure is calibrated, not guessed. Recalibrated after the
    # corpus grew to 74 passages, which is exactly the re-check this comment
    # used to warn was needed: scores had shifted and the same floor started
    # rejecting correct answers.
    #
    # At that size, lexical matching alone was not merely mis-scoring but
    # returning nonsense — "is it safe to breastfeed at night" retrieved the
    # asthma passage, because no threshold fixes a vocabulary mismatch. Adding
    # stemming to the tokeniser fixed the retrieval itself, and the numbers
    # then separated cleanly: across nine on-topic queries the lowest score is
    # 0.155 and across five off-topic queries the highest is 0.122, with every
    # on-topic query returning the correct passage. 0.15 sits in that gap.
    MIN_RELEVANCE = 0.35
    MIN_RELEVANCE_TFIDF = 0.15

    @property
    def min_relevance(self) -> float:
        """The floor appropriate to whichever backend actually answered."""
        return (
            self.MIN_RELEVANCE_TFIDF
            if self._backend.startswith("tfidf")
            else self.MIN_RELEVANCE
        )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False
        self._backend = "uninitialised"
        self._client = None
        self._embedder = None
        self._tfidf: _TfidfIndex | None = None
        self._docs: dict[str, IndexedDoc] = {}
        self._counts: dict[str, int] = {}
        self._mode = ""
        self._fallback_reason = ""

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
                self._fallback_reason = str(exc)
                # Name the cause and the fix. "Already accessed by another
                # instance" is the common one and it is not a failure at all —
                # it means the API server holds the embedded store and this is
                # a second process. Left as a generic warning, it reads like
                # noise and gets ignored, which is how the fallback ended up
                # being the path nobody was testing.
                if "already accessed" in self._fallback_reason.lower():
                    logger.warning(
                        "Embedded Qdrant is locked by another process, so this "
                        "one is using the weaker TF-IDF index. Both processes "
                        "can share a server instead: `docker compose up -d "
                        "qdrant` — it is detected automatically, no config "
                        "change needed."
                    )
                else:
                    logger.warning(
                        "Qdrant/MiniLM retrieval unavailable (%s); "
                        "falling back to the in-process TF-IDF index.",
                        exc,
                    )
                self._load_documents()
                self._tfidf = _TfidfIndex(list(self._docs.values()))
                self._backend = "tfidf-fallback"
            self._ready = True

    def _load_documents(self, db=None) -> None:
        """Assemble every collection's documents.

        The provider directory needs a database session. When one is not
        available — a unit test, an offline tool — that collection is simply
        empty rather than the whole service failing.
        """
        docs = clinical_documents() + policy_documents()

        if db is not None:
            docs += provider_documents(db)
        else:
            try:
                from app.core.db import SessionLocal

                session = SessionLocal()
                try:
                    docs += provider_documents(session)
                finally:
                    session.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Provider directory not indexed (%s); "
                    "clinical and policy retrieval still work.", exc
                )

        self._docs = {doc.id: doc for doc in docs}
        self._counts = {
            name: sum(1 for d in docs if d.collection == name) for name in COLLECTIONS
        }

    def _init_qdrant(self, *, force: bool = False) -> None:
        from fastembed import TextEmbedding
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, PointStruct, VectorParams

        if self._embedder is None:
            self._embedder = TextEmbedding(model_name=settings.embedding_model)

        if self._client is None:
            url = settings.qdrant_url or _detect_local_server()
            if url:
                self._client = QdrantClient(url=url)
                self._mode = f"server {url}"
            else:
                # Local on-disk mode: real Qdrant semantics, no server to run,
                # and an exclusive file lock. One process at a time.
                self._client = QdrantClient(path=str(settings.qdrant_path))
                self._mode = "embedded"

        self._load_documents()
        existing = {c.name for c in self._client.get_collections().collections}

        for name in COLLECTIONS:
            docs = [d for d in self._docs.values() if d.collection == name]
            if not docs:
                continue

            vectors = list(self._embedder.embed([d.text for d in docs]))
            dimension = len(vectors[0])
            qdrant_name = self._collection_name(name)

            if qdrant_name not in existing:
                self._client.create_collection(
                    collection_name=qdrant_name,
                    vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
                )
            elif force:
                # A provider was added or removed: recreate so deletions
                # actually disappear. Upsert alone would leave stale rows.
                self._client.delete_collection(collection_name=qdrant_name)
                self._client.create_collection(
                    collection_name=qdrant_name,
                    vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
                )

            # Upsert is idempotent, so a restart re-syncs cheaply.
            self._client.upsert(
                collection_name=qdrant_name,
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
                            "collection": doc.collection,
                            "text": doc.text,
                        },
                    )
                    for index, (doc, vector) in enumerate(zip(docs, vectors))
                ],
            )

    def _collection_name(self, logical: str) -> str:
        """Namespace collections under the configured prefix."""
        base = settings.qdrant_collection.removesuffix("_health_knowledge")
        return f"{base}_{logical}"

    def reindex(self, db=None) -> dict[str, int]:
        """Rebuild every collection from source. Safe to call at any time.

        This is what makes the provider directory live: run it after seeding
        or after an admin adds a doctor.
        """
        with self._lock:
            self._ready = False
            try:
                self._init_qdrant(force=True)
                self._backend = "qdrant+minilm"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Reindex fell back to TF-IDF (%s).", exc)
                self._load_documents(db)
                self._tfidf = _TfidfIndex(list(self._docs.values()))
                self._backend = "tfidf-fallback"
            self._ready = True
            return dict(self._counts)

    # -- query -------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        limit: int = 4,
        topic: str | None = None,
        collection: str = CLINICAL,
    ) -> list[RetrievedDoc]:
        if not (query or "").strip():
            return []
        self.ensure_ready()

        if self._backend.startswith("qdrant"):
            try:
                return self._search_qdrant(
                    query, limit=limit, topic=topic, collection=collection
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Qdrant query failed (%s); using TF-IDF fallback.", exc)
                if self._tfidf is None:
                    self._tfidf = _TfidfIndex(list(self._docs.values()))

        index = self._tfidf or _TfidfIndex(list(self._docs.values()))
        results = index.search(
            query, limit * 3 if topic else limit, collection=collection
        )
        if topic:
            results = [r for r in results if r.doc.topic == topic]
        return results[:limit]

    def _search_qdrant(
        self, query: str, *, limit: int, topic: str | None, collection: str
    ) -> list[RetrievedDoc]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        vector = list(self._embedder.embed([query]))[0].tolist()
        query_filter = (
            Filter(must=[FieldCondition(key="topic", match=MatchValue(value=topic))])
            if topic
            else None
        )
        hits = self._client.search(
            collection_name=self._collection_name(collection),
            query_vector=vector,
            query_filter=query_filter,
            limit=limit,
        )

        out: list[RetrievedDoc] = []
        for hit in hits:
            payload = hit.payload or {}
            # Prefer the in-memory document so structured `payload` (which is
            # not stored in Qdrant) comes along for the UI.
            doc = self._docs.get(payload.get("doc_id", ""))
            if doc is None:
                doc = IndexedDoc(
                    id=payload.get("doc_id", ""),
                    title=payload.get("title", ""),
                    topic=payload.get("topic", ""),
                    text=payload.get("text", ""),
                    source=payload.get("source", ""),
                    collection=payload.get("collection", collection),
                )
            out.append(RetrievedDoc(doc, float(hit.score)))
        return out

    def build_context(
        self,
        query: str,
        *,
        limit: int = 3,
        min_score: float | None = None,
        collection: str = CLINICAL,
    ) -> tuple[str, list[dict]]:
        """Return (grounding text, citations) for injection into an LLM prompt.

        Returns empty when nothing is relevant, so the caller can say it does
        not know rather than grounding on noise.
        """
        floor = self.min_relevance if min_score is None else min_score
        results = [
            r for r in self.search(query, limit=limit, collection=collection)
            if r.score >= floor
        ]
        if not results:
            return "", []
        # Titles only — the internal document id must never reach a patient.
        # Citations are returned alongside for the UI to render properly.
        blocks = [f"{r.doc.title}\n{r.doc.text}" for r in results]
        return "\n\n".join(blocks), [r.to_citation() for r in results]

    def search_providers(
        self, query: str, *, limit: int = 6, min_score: float | None = None
    ) -> list[RetrievedDoc]:
        """Directory search returning results with their structured payloads."""
        floor = self.min_relevance if min_score is None else min_score
        return [
            r for r in self.search(query, limit=limit, collection=PROVIDERS)
            if r.score >= floor
        ]

    @property
    def backend(self) -> str:
        self.ensure_ready()
        return self._backend

    def stats(self) -> dict:
        self.ensure_ready()
        stats = {
            "backend": self._backend,
            "mode": self._mode or "n/a",
            "collections": dict(self._counts),
            "total_documents": len(self._docs),
            "min_relevance": self.min_relevance,
        }
        # Say why the weaker index is in use, and what to do about it. A
        # status endpoint reporting "tfidf-fallback" with no reason gives an
        # operator nothing to act on, and this particular fallback is usually
        # one command away from being fixed.
        if self._backend.startswith("tfidf"):
            stats["degraded"] = True
            stats["reason"] = self._fallback_reason[:200] or "unknown"
            if "already accessed" in self._fallback_reason.lower():
                stats["fix"] = (
                    "Embedded Qdrant allows one process at a time. Run "
                    "`docker compose up -d qdrant` — it is detected "
                    "automatically."
                )
        return stats


knowledge_service = KnowledgeService()
