"""Cache-Augmented Generation: answer before you generate.

A large share of what patients type is not a clinical question at all. It is
"hi", "thanks", "what is SuwaPath", "how do I book". Sending those to a model
costs a second of latency and a slice of a free-tier quota to produce a worse,
less consistent answer than a written one.

Two cache layers sit in front of the graph:

**Layer 1 — canned.** Greetings, thanks, and product FAQs are matched by
normalised text and by embedding similarity. These answers are written once,
reviewed, and returned verbatim. For a medical product this is a safety
feature as much as a speed one: the answer to "are my messages private?" must
be identical every time and must never be improvised.

**Layer 2 — personal.** Per-user, per-session memory of facts the patient has
already given (age band, pregnancy, chronic conditions, what they came in
about). This is what stops the assistant asking "how old are you?" three
turns running.

What is deliberately *not* cached
---------------------------------
Anything clinical. Two patients asking "is this chest pain serious?" have
different answers, and a semantic cache cannot see the difference — the
embeddings are nearly identical while the correct responses diverge
completely. Only the non-clinical intents above are eligible, enforced by
``_CACHEABLE`` rather than by a similarity threshold. Confidential-mode
sessions bypass both layers entirely and write nothing.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Cosine similarity at or above this counts as the same question. Set high on
# purpose: a false hit returns a confidently wrong canned answer, which is far
# worse than the ~600ms a miss costs.
_SIMILARITY_THRESHOLD = 0.88

# Personal memory is session-scoped and evaporates; it is not a medical record.
_MEMORY_TTL_SECONDS = 60 * 60 * 6
_MEMORY_MAX_SESSIONS = 500


@dataclass(slots=True)
class CacheHit:
    answer: str
    key: str
    kind: str                    # greeting | faq | capability
    score: float
    latency_ms: int
    suggestions: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Layer 1 — reviewed answers for non-clinical intents
# --------------------------------------------------------------------------
# `answer` is markdown; the UI renders it. Each entry lists the phrasings we
# expect, which are what the embeddings are built from.
_CACHEABLE: list[dict[str, Any]] = [
    {
        "key": "greeting",
        "kind": "greeting",
        "phrasings": [
            "hi", "hello", "hey", "good morning", "good afternoon",
            "good evening", "ayubowan", "hi there", "hello there",
        ],
        "answer": (
            "Hello — I'm the SuwaPath assistant.\n\n"
            "You can tell me what's bothering you in your own words, and I'll "
            "ask a few questions the way a doctor would before pointing you to "
            "the right care.\n\n"
            "**I can help you with**\n"
            "- Working through a symptom and how urgent it is\n"
            "- Understanding a report or scan you've uploaded\n"
            "- Finding and booking the right doctor or hospital\n"
            "- General health questions\n\n"
            "What's going on?"
        ),
        "suggestions": [
            "I've had a headache for three days",
            "Explain my latest blood test",
            "Find me a doctor nearby",
        ],
    },
    {
        "key": "thanks",
        "kind": "greeting",
        "phrasings": [
            "thanks", "thank you", "thanks a lot", "thank you so much",
            "cheers", "appreciate it", "bohoma sthuthi",
        ],
        "answer": (
            "You're welcome. If anything changes or gets worse, come back and "
            "tell me — it's easier to help early.\n\n"
            "Take care of yourself."
        ),
        "suggestions": [],
    },
    {
        "key": "what_is_suwapath",
        "kind": "faq",
        "phrasings": [
            "what is suwapath", "what do you do", "who are you",
            "what can you help me with", "what are you", "how does this work",
            "what can you do",
        ],
        "answer": (
            "I'm SuwaPath's health assistant — think of me as the step before "
            "the clinic, not a replacement for it.\n\n"
            "**What I do**\n"
            "- **Listen properly.** You describe a symptom, I ask the "
            "follow-up questions a doctor would, rather than guessing from one "
            "sentence.\n"
            "- **Explain.** Reports, scans and test results in plain language.\n"
            "- **Point you somewhere useful.** The right specialty, how soon, "
            "which tests are worth doing, and a doctor or hospital that can "
            "actually see you.\n\n"
            "**What I don't do**\n"
            "I don't diagnose and I don't prescribe. When I'm unsure I'll say "
            "so plainly — you should treat anything here as a starting point "
            "that a clinician confirms."
        ),
        "suggestions": [
            "I have a symptom I want to check",
            "How do I book an appointment?",
        ],
    },
    {
        "key": "privacy",
        "kind": "faq",
        "phrasings": [
            "is my data private", "who can see my data", "is this confidential",
            "do you share my information", "is this secure",
            "what happens to my data", "do you send my data to ai",
        ],
        "answer": (
            "A fair thing to ask, and here is the honest version.\n\n"
            "**What stays on this system, always**\n"
            "Urgency assessment, report reading, and image screening run "
            "locally. Your name, contact details, NIC and exact location are "
            "never sent to a language model — they're stripped before anything "
            "leaves, and your age is coarsened to a band rather than a number.\n\n"
            "**What a language model sees**\n"
            "Only the minimum needed for the specific question, with names "
            "replaced by placeholders. If an identifier somehow survives that, "
            "the request is blocked rather than sent.\n\n"
            "**Sharing with people**\n"
            "Nobody sees your records unless you grant it, and you can withdraw "
            "that at any time under **Sharing & Consent**. Guardians only see "
            "the specific categories you've ticked.\n\n"
            "**Private mode**\n"
            "For anything sensitive, start a private chat — it's never written "
            "to your history, and only a PIN you choose can resume it."
        ),
        "suggestions": ["Start a private chat", "Show my sharing settings"],
    },
    {
        "key": "how_to_book",
        "kind": "capability",
        "phrasings": [
            "how do i book an appointment", "how to book", "how do i see a doctor",
            "how do i channel a doctor", "how do i make an appointment",
        ],
        "answer": (
            "You can do it right here — just tell me what you need.\n\n"
            "**Either**\n"
            "- Say something like *\"find me a dermatologist in Colombo\"* and "
            "I'll show you who's available with their next free slot, or\n"
            "- Describe your symptom and I'll work out which specialty you "
            "actually need first, so you don't pay to see the wrong one.\n\n"
            "You can also browse everything under **Doctors & Hospitals**."
        ),
        "suggestions": [
            "Find me a doctor nearby",
            "Which specialist do I need?",
        ],
    },
    {
        "key": "emergency_numbers",
        "kind": "faq",
        "phrasings": [
            "what is the emergency number", "ambulance number",
            "who do i call in an emergency", "emergency contact sri lanka",
        ],
        "answer": (
            "**If someone is in danger right now, call these — don't wait for me.**\n\n"
            "- **1990** — Suwa Seriya ambulance (free, nationwide)\n"
            "- **110** — Police emergency\n"
            "- **1926** — National Mental Health Helpline (24 hours)\n"
            "- **1333** — Sumithrayo emotional support\n\n"
            "Go to the nearest emergency department for chest pain, difficulty "
            "breathing, heavy bleeding, sudden weakness or one-sided face droop, "
            "or a first fit."
        ),
        "suggestions": [],
    },
    {
        "key": "languages",
        "kind": "faq",
        "phrasings": [
            "what languages do you speak", "can you speak sinhala",
            "do you support tamil", "can i type in sinhala",
        ],
        "answer": (
            "Yes — **English, සිංහල and தமிழ்**. Type in whichever you're most "
            "comfortable with and I'll reply in the same language.\n\n"
            "You can also switch the whole app's language from your profile."
        ),
        "suggestions": [],
    },
]

# Only these intents may ever be answered from cache.
_CACHEABLE_KINDS = frozenset({"greeting", "faq", "capability"})

_PUNCT = re.compile(r"[^\w\s]+")
_WS = re.compile(r"\s+")


def _normalise(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


class _CanonicalCache:
    """Exact-match then embedding-match over the reviewed answers above."""

    def __init__(self) -> None:
        self._exact: dict[str, dict] = {}
        self._vectors: list[tuple[dict, Any]] = []
        self._embedder = None
        self._ready = False
        self._lock = threading.Lock()

        for entry in _CACHEABLE:
            for phrasing in entry["phrasings"]:
                self._exact[_normalise(phrasing)] = entry

    def _ensure_vectors(self) -> None:
        """Build phrase embeddings once, lazily — the model load is ~1s."""
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            try:
                from app.services.knowledge import knowledge_service

                knowledge_service.ensure_ready()
                embedder = getattr(knowledge_service, "_embedder", None)
                if embedder is None:
                    self._ready = True          # exact matching only
                    return
                phrases, owners = [], []
                for entry in _CACHEABLE:
                    for phrasing in entry["phrasings"]:
                        phrases.append(phrasing)
                        owners.append(entry)
                vectors = list(embedder.embed(phrases))
                self._vectors = list(zip(owners, vectors))
                self._embedder = embedder
            except Exception as exc:  # noqa: BLE001
                logger.warning("CAG embeddings unavailable (%s); exact match only.", exc)
            finally:
                self._ready = True

    def lookup(self, text: str) -> tuple[dict, float] | None:
        normalised = _normalise(text)
        if not normalised:
            return None

        entry = self._exact.get(normalised)
        if entry:
            return entry, 1.0

        # Only short messages are considered for semantic matching. A long
        # message is doing something more specific than "hello", even if it
        # opens with one.
        if len(normalised.split()) > 12:
            return None

        self._ensure_vectors()
        if not self._vectors or self._embedder is None:
            return None

        try:
            query = list(self._embedder.embed([text]))[0]
        except Exception:  # noqa: BLE001
            return None

        best, best_score = None, 0.0
        for owner, vector in self._vectors:
            score = _cosine(query, vector)
            if score > best_score:
                best, best_score = owner, score

        if best is not None and best_score >= _SIMILARITY_THRESHOLD:
            return best, round(best_score, 3)
        return None


def _cosine(a, b) -> float:
    dot = norm_a = norm_b = 0.0
    for x, y in zip(a, b):
        dot += float(x) * float(y)
        norm_a += float(x) * float(x)
        norm_b += float(y) * float(y)
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / ((norm_a**0.5) * (norm_b**0.5))


_canonical = _CanonicalCache()


def lookup(text: str, *, confidential: bool = False) -> CacheHit | None:
    """Return a reviewed answer when the message is a known non-clinical one."""
    if confidential:
        # Private mode reads nothing and writes nothing. Even a cache read is
        # an observable side effect we would rather not have.
        return None

    started = time.perf_counter()
    found = _canonical.lookup(text)
    if not found:
        return None

    entry, score = found
    if entry["kind"] not in _CACHEABLE_KINDS:
        return None

    return CacheHit(
        answer=entry["answer"],
        key=entry["key"],
        kind=entry["kind"],
        score=score,
        latency_ms=int((time.perf_counter() - started) * 1000),
        suggestions=list(entry.get("suggestions", [])),
    )


# --------------------------------------------------------------------------
# Layer 2 — per-session personal memory
# --------------------------------------------------------------------------
@dataclass
class SessionMemory:
    """What the patient has already told us this session.

    Held in process memory with a TTL, never written to the database. It
    exists so the assistant does not re-ask what it was just told; it is not
    a clinical record and nothing here is authoritative.
    """

    session_id: str
    facts: dict[str, Any] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)
    chief_complaint: str | None = None
    updated_at: float = field(default_factory=time.monotonic)

    def note(self, key: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        self.facts[key] = value
        self.updated_at = time.monotonic()

    def mark_asked(self, topic: str) -> None:
        if topic and topic not in self.asked:
            self.asked.append(topic)
        self.updated_at = time.monotonic()

    def as_context(self) -> str:
        """Render for a prompt. Contains no identifiers by construction."""
        if not self.facts and not self.chief_complaint:
            return ""
        lines = []
        if self.chief_complaint:
            lines.append(f"Came in about: {self.chief_complaint}")
        for key, value in self.facts.items():
            label = key.replace("_", " ")
            lines.append(f"{label}: {value}")
        return "Already established this conversation:\n" + "\n".join(
            f"- {line}" for line in lines
        )


class _MemoryStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionMemory] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str, *, confidential: bool = False) -> SessionMemory:
        if confidential:
            # A throwaway that is never stored, so private turns still get
            # continuity within the request but leave nothing behind.
            return SessionMemory(session_id=session_id)
        with self._lock:
            self._evict()
            memory = self._sessions.get(session_id)
            if memory is None:
                memory = SessionMemory(session_id=session_id)
                self._sessions[session_id] = memory
            return memory

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _evict(self) -> None:
        now = time.monotonic()
        stale = [
            key for key, memory in self._sessions.items()
            if now - memory.updated_at > _MEMORY_TTL_SECONDS
        ]
        for key in stale:
            self._sessions.pop(key, None)
        if len(self._sessions) > _MEMORY_MAX_SESSIONS:
            for key in sorted(
                self._sessions, key=lambda k: self._sessions[k].updated_at
            )[: len(self._sessions) - _MEMORY_MAX_SESSIONS]:
                self._sessions.pop(key, None)


memory_store = _MemoryStore()


def status() -> dict:
    return {
        "canonical_entries": len(_CACHEABLE),
        "phrasings": sum(len(e["phrasings"]) for e in _CACHEABLE),
        "similarity_threshold": _SIMILARITY_THRESHOLD,
        "cacheable_kinds": sorted(_CACHEABLE_KINDS),
        "clinical_cached": False,
        "live_sessions": len(memory_store._sessions),  # noqa: SLF001
    }
