"""Guarded web search.

The assistant needs current public information — a dengue advisory, a changed
clinic fee, a drug recall — that no seeded knowledge base can hold. Handing a
medical chatbot an unfiltered search tool is, however, a good way to launder
whatever the open web says into something that looks like clinical advice.

Three constraints therefore apply, in this order:

1. **The query never carries patient data.** It is rebuilt from the topic, not
   forwarded verbatim, and passed through the PHI egress guard before it
   leaves. Search queries are logged by the provider indefinitely.
2. **Sources are ranked, not accepted.** Health ministries, WHO and major
   reference sites outrank a content farm; results below the floor are
   dropped rather than shown with a caveat.
3. **Results are evidence, never authority.** Nothing retrieved here can set
   urgency or contradict the red-flag engine. Snippets that read as
   instructions — dosages, "take X mg" — are stripped, because the model will
   happily repeat them otherwise.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.privacy.boundary import EgressBlocked, PHIBoundary

logger = logging.getLogger(__name__)

_TIMEOUT = 12.0
_MAX_RESULTS = 5

# Domains whose health guidance is worth surfacing to a patient. Scores are
# relative, not absolute truth — they decide ordering and the cut-off.
_DOMAIN_SCORES: dict[str, float] = {
    # Sri Lankan authorities first: this is a Sri Lankan product and local
    # guidance (dengue serotypes, NMRA drug status) beats generic advice.
    "health.gov.lk": 1.0,
    "epid.gov.lk": 1.0,
    "nmra.gov.lk": 1.0,
    "slma.lk": 0.95,
    "moh.gov.lk": 1.0,
    # International public health.
    "who.int": 1.0,
    "cdc.gov": 0.95,
    "nih.gov": 0.95,
    "ncbi.nlm.nih.gov": 0.95,
    "nhs.uk": 0.95,
    "mayoclinic.org": 0.85,
    "medlineplus.gov": 0.9,
    "bmj.com": 0.9,
    "thelancet.com": 0.9,
    "cochrane.org": 0.9,
    "unicef.org": 0.85,
    "un.org": 0.8,
    # Reputable general press, useful for outbreak news but not for guidance.
    "reliefweb.int": 0.7,
    "reuters.com": 0.6,
    "bbc.com": 0.6,
}

# Anything at or below this is not shown at all.
_SCORE_FLOOR = 0.5
_UNKNOWN_DOMAIN_SCORE = 0.45

# Snippets containing these read as prescriptions once a model paraphrases
# them. The sentence is dropped, not the whole result.
#
# This is deliberately over-broad. A dropped sentence costs the patient a
# little context; a kept one can become "SuwaPath told me to take 8 tablets".
_INSTRUCTIONAL = re.compile(
    # Up to two words may sit between the number and the unit
    # ("2 extra tablets", "500 mg soluble tablets").
    r"\b\d+\s*(?:\w+\s+){0,2}(?:mg|mcg|ml|g|iu|tablets?|capsules?|drops?|puffs?)\b"
    r"|\btake\s+(?:\d|one|two|three|four|no\s+more)"
    r"|\b(?:dose|doses|dosage|dosing|overdose)\b"
    r"|\btimes?\s+(?:a|per|each)\s+(?:day|week)\b"
    r"|\bevery\s+\d+\s*(?:hour|hr|day|minute)s?\b"
    r"|\b(?:wait|leave)\s+(?:at\s+least\s+)?\d+\s*(?:hour|hr|minute|day)s?\b"
    r"|\bin\s+24\s*hours?\b"
    r"|\bmaximum\s+daily\b"
    r"|\bhow\s+(?:much|often)\s+(?:can|should|to)\b",
    re.IGNORECASE,
)

# Cookie banners and nav furniture that crawlers pick up as "content".
_BOILERPLATE = re.compile(
    r"cookies?|terms\s*&?\s*conditions|privacy\s+polic|opens\s+new\s+tab"
    r"|all\s+rights\s+reserved|skip\s+to\s+(?:main|content)|sign\s+in"
    r"|©|\bcopyright\b|\bcorrections\b|\bsubscribe\b|\bnewsletter\b"
    r"|(?:image|figure)\s*\d+|\badvertisement\b|\bfollow\s+us\b",
    re.IGNORECASE,
)

# Search is for public information. If a query looks like it is about one
# identifiable person, it does not go out at all.
#
# `\bmy\b .. \b(report|result|…)` allows intervening words so that
# "my blood test results" is caught as readily as "my results".
_PERSONAL = re.compile(
    r"\bmy\b(?:\W+\w+){0,3}\W+"
    r"(?:report|result|test|scan|x-?ray|appointment|prescription|record|"
    r"medication|diagnosis|history|chart|referral)s?\b"
    r"|\b(?:i|i've|i have|im|i'm)\b.{0,20}\b(?:diagnosed|prescribed)\b"
    r"|\b\d{9}[vVxX]\b"                      # Sri Lankan NIC (old format)
    r"|\b(?:19|20)\d{10}\b"                  # Sri Lankan NIC (new format)
    r"|[\w.+-]+@[\w-]+\.[\w.]+"
    r"|\b(?:\+94|0)\d{9}\b",                 # Sri Lankan phone number
    re.IGNORECASE,
)


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str
    score: float          # domain trust — decides admissibility
    relevance: float = 0.5  # the engine's own match score
    rank: float = 0.0       # blended, decides order


@dataclass(slots=True)
class SearchOutcome:
    results: list[SearchResult] = field(default_factory=list)
    status: str = "ok"          # ok | disabled | blocked | empty | error
    detail: str = ""
    latency_ms: int = 0
    query: str = ""

    def __bool__(self) -> bool:
        return bool(self.results)


def _domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _score_for(domain: str) -> float:
    if domain in _DOMAIN_SCORES:
        return _DOMAIN_SCORES[domain]
    # Match subdomains of known authorities (e.g. apps.who.int).
    for known, score in _DOMAIN_SCORES.items():
        if domain.endswith(f".{known}"):
            return score
    # Government and academic domains get the benefit of the doubt.
    if domain.endswith((".gov", ".gov.lk", ".edu", ".ac.lk")):
        return 0.8
    return _UNKNOWN_DOMAIN_SCORE


def _clean_snippet(text: str) -> str:
    """Drop sentences that read as dosing instructions or page furniture."""
    # Crawled text keeps markdown headings and list bullets, which run
    # sentences together; split on those as well as on terminators.
    fragments = re.split(r"(?<=[.!?])\s+|\n+|\s*#{1,6}\s*|\s*\[\.\.\.\]\s*", text or "")
    kept = [
        fragment.strip()
        for fragment in fragments
        if fragment
        and fragment.strip()
        and not _INSTRUCTIONAL.search(fragment)
        and not _BOILERPLATE.search(fragment)
    ]
    return " ".join(kept)[:400].strip()


# Words that mean "what is true today", which a search engine cannot infer
# from the question alone.
_CURRENCY = re.compile(
    r"\b(right now|currently|current|now|today|latest|recent|recently|"
    r"this year|at the moment|ongoing|these days|nowadays)\b", re.I)

# Conversational framing that dilutes the query without adding meaning.
_FRAMING = re.compile(
    r"^\s*(is|are|was|were|do|does|did|can|could|should|would|has|have|"
    r"what|why|how|when|where|which|who|tell me|i want to know|"
    r"can you tell me|please)\b[\s,]*"
    r"|\b(there|it|a|an|the)\s+(is|are)\b"
    r"|\?+\s*$",
    re.I)

# If the question already names a place, do not override it.
_HAS_PLACE = re.compile(
    r"\b(sri lanka|lanka|colombo|kandy|galle|jaffna|negombo|kurunegala|"
    r"anuradhapura|batticaloa|matara|gampaha|india|world|global|worldwide|"
    r"usa|uk|europe|asia|africa|australia|america)\b", re.I)

DEFAULT_REGION = "Sri Lanka"


def build_query(question: str, *, region: str = DEFAULT_REGION) -> str:
    """Turn a patient's message into a search query worth running.

    Forwarding the message verbatim is how "is there a dengue outbreak right
    now?" came back with a generic CDC page about dengue as a global disease —
    and the assistant then told a Sri Lankan patient, during an actual Sri
    Lankan outbreak, that there was no information about one. The engine had
    no way to know which country was meant.

    So the query is rebuilt rather than forwarded: framing is stripped, and
    the region and year are supplied when the question is about current
    conditions and does not name a place itself.
    """
    text = (question or "").strip()
    if not text:
        return ""

    wants_current = bool(_CURRENCY.search(text))

    # Strip framing repeatedly — "is there a…" needs two passes.
    core = text
    for _ in range(3):
        stripped = _FRAMING.sub(" ", core).strip()
        if stripped == core:
            break
        core = stripped
    core = " ".join(core.split()) or text

    parts = [core]
    if region and not _HAS_PLACE.search(text):
        parts.append(region)
    if wants_current:
        parts.append(str(datetime.now().year))

    return " ".join(parts)[:300]


def search(topic: str, *, session_salt: str = "search", max_results: int = 4) -> SearchOutcome:
    """Search reputable sources for a *topic*. Never raises.

    ``topic`` should already be a general subject ("dengue outbreak Colombo"),
    not the patient's raw message.
    """
    started = time.perf_counter()
    query = (topic or "").strip()

    if not query:
        return SearchOutcome(status="empty", detail="No topic supplied.")

    if not settings.tavily_enabled:
        return SearchOutcome(status="disabled", detail="Web search is not configured.")

    if _PERSONAL.search(query):
        # This is the layer that matters most: a search query is a permanent,
        # third-party-held record. Personal detail must not reach it.
        logger.info("Web search refused: query looked personal.")
        return SearchOutcome(
            status="blocked",
            detail="That question is about your own records, so it is answered "
                   "from your SuwaPath data rather than the web.",
            query="",
        )

    boundary = PHIBoundary(session_salt=session_salt, route="knowledge")
    try:
        boundary.guard(query)
    except EgressBlocked as exc:
        logger.warning("Web search blocked by PHI boundary: %s", exc)
        return SearchOutcome(status="blocked", detail="Query withheld for privacy.", query="")

    try:
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "max_results": min(max_results * 2, _MAX_RESULTS * 2),
                "search_depth": "basic",
                "include_answer": False,
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily search failed: %s", exc)
        return SearchOutcome(
            status="error",
            detail="Web search is temporarily unavailable.",
            latency_ms=int((time.perf_counter() - started) * 1000),
            query=query,
        )

    results: list[SearchResult] = []
    for item in payload.get("results", []):
        url = item.get("url") or ""
        domain = _domain_of(url)
        trust = _score_for(domain)
        if trust < _SCORE_FLOOR:
            continue
        snippet = _clean_snippet(item.get("content", ""))
        if not snippet:
            continue

        # Trust decides whether a source is admissible; relevance decides the
        # order among admissible ones. Ranking on trust alone put WHO's
        # general dengue page above a Sri Lankan ministry outbreak bulletin,
        # and the model then reported there was no outbreak.
        relevance = float(item.get("score") or 0.5)
        results.append(SearchResult(
            title=(item.get("title") or domain)[:160],
            url=url,
            snippet=snippet,
            domain=domain,
            score=round(trust, 3),
            relevance=round(relevance, 3),
            rank=round(0.45 * trust + 0.55 * relevance, 4),
        ))

    results.sort(key=lambda r: -r.rank)
    results = results[:max_results]

    return SearchOutcome(
        results=results,
        status="ok" if results else "empty",
        detail="" if results else "No sufficiently reliable source was found.",
        latency_ms=int((time.perf_counter() - started) * 1000),
        query=query,
    )


def as_context(outcome: SearchOutcome) -> str:
    """Render results for a prompt, with provenance attached to each claim."""
    if not outcome.results:
        return ""
    lines = ["Current public sources (may be cited, never used to set urgency):"]
    for index, result in enumerate(outcome.results, start=1):
        lines.append(f"[W{index}] {result.title} — {result.domain}\n{result.snippet}")
    return "\n".join(lines)


def as_citations(outcome: SearchOutcome) -> list[dict]:
    return [
        {
            "id": f"W{index}",
            "title": result.title,
            "url": result.url,
            "source": result.domain,
            "kind": "web",
        }
        for index, result in enumerate(outcome.results, start=1)
    ]
