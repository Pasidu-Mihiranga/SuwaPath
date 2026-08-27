"""Reading and writing durable patient memory.

Extraction runs *after* a turn has been answered, not before, so it never adds
latency to the reply the patient is waiting for. It is deliberately
conservative: a fact is only recorded when the patient stated it about
themselves, and the extractor is given a closed list of keys rather than being
allowed to invent them. An open-ended "remember anything interesting" pass
produces a memory store full of half-hallucinated clinical claims, which is
exactly what must not happen here.

Deterministic patterns run first and are trusted more than the model. "I'm
allergic to penicillin" is a regex, not a reasoning problem.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.memory import MemoryKind, PatientMemory
from app.services import llm

logger = logging.getLogger(__name__)

# Facts the assistant is allowed to remember. A closed list is the main
# defence against the store filling with invented clinical detail.
ALLOWED_KEYS: dict[str, str] = {
    "allergy": MemoryKind.CLINICAL,
    "chronic_condition": MemoryKind.CLINICAL,
    "past_procedure": MemoryKind.CLINICAL,
    "regular_medication": MemoryKind.CLINICAL,
    "pregnancy": MemoryKind.CONTEXT,
    "caring_for": MemoryKind.CONTEXT,
    "lives_alone": MemoryKind.PROFILE,
    "occupation": MemoryKind.PROFILE,
    "transport_difficulty": MemoryKind.PROFILE,
    "preferred_language": MemoryKind.PREFERENCE,
    "prefers_teleconsultation": MemoryKind.PREFERENCE,
    "preferred_area": MemoryKind.PREFERENCE,
}

# Context facts go stale. A pregnancy noted eleven months ago should not still
# be asserted to a model.
_TTL_DAYS = {MemoryKind.CONTEXT: 270, MemoryKind.EPISODIC: 90}

_MAX_FACTS_IN_PROMPT = 8

# High-confidence deterministic extraction. First person only — "my mother is
# diabetic" is about someone else and must not become the patient's condition.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("allergy", re.compile(
        r"\bi(?:'m| am)?\s+allergic\s+to\s+([a-z][a-z\s\-]{2,40})", re.I)),
    ("allergy", re.compile(
        r"\bi\s+(?:also\s+|now\s+)?have\s+an?\s+allergy\s+to\s+([a-z][a-z\s\-]{2,40})", re.I)),
    # Capturing group deliberate. Without one, `match.groups()` is empty and
    # the value falls back to the literal "yes" — so "I'm diabetic" was stored
    # as `chronic_condition: yes`, which tells a later consultation nothing at
    # all. The alternation captures a normalised condition name either way.
    ("chronic_condition", re.compile(
        r"\bi(?:'m| am)?\s+(?:a\s+)?(diabetic)\b|\bi\s+have\s+(diabetes)\b", re.I)),
    ("chronic_condition", re.compile(
        r"\bi\s+(?:also\s+|now\s+)?have\s+(asthma|hypertension|high blood pressure|epilepsy|"
        r"thyroid problems?|kidney disease|heart disease)\b", re.I)),
    ("lives_alone", re.compile(r"\bi\s+live\s+alone\b", re.I)),
    ("transport_difficulty", re.compile(
        r"\bi\s+(?:can'?t|cannot)\s+travel\b|\bi\s+have\s+no\s+(?:transport|vehicle)\b",
        re.I)),
    ("prefers_teleconsultation", re.compile(
        r"\bi\s+(?:prefer|would rather have)\s+(?:an?\s+)?(?:online|video|tele)"
        r"\s*(?:consultation|appointment|call)?\b", re.I)),
]

# How the patient says it, mapped to what a clinician would write down.
_NORMALISE = {
    "diabetic": "diabetes",
    "high blood pressure": "hypertension",
    "thyroid problem": "thyroid disease",
    "thyroid problems": "thyroid disease",
}

# Keys a patient can legitimately have several of. Stored in one row as a
# comma-separated list, because `remember` matches on (patient, key) and would
# otherwise let "I have diabetes and asthma" overwrite itself down to one
# condition — and a consultation that knows about only half of someone's
# history is worse than one that knows none of it, because it looks complete.
_MULTI_VALUED = {"allergy", "chronic_condition", "regular_medication", "past_procedure"}


# Trailing filler the greedy captures drag in: "allergic to aspirin as well"
# stored the allergy as "aspirin as well", which then never matches the same
# allergy stated plainly and accumulates as a second entry.
_TRAILING_FILLER = re.compile(
    r"\s+(?:as\s+well|too|also|and.*|but.*|which.*|since.*|because.*)$", re.I)


def _clean_value(raw: str | None) -> str:
    value = (raw or "").strip().rstrip(".,;:")
    value = _TRAILING_FILLER.sub("", value).strip()
    return _NORMALISE.get(value.lower(), value)


def _merge_values(existing: str, addition: str) -> str:
    """Add a value to a list-valued memory, preserving order, ignoring repeats."""
    parts = [p.strip() for p in (existing or "").split(",") if p.strip()]
    lowered = {p.lower() for p in parts}
    for candidate in [p.strip() for p in addition.split(",") if p.strip()]:
        if candidate.lower() not in lowered:
            parts.append(candidate)
            lowered.add(candidate.lower())
    return ", ".join(parts)[:400]


# Never remember something the patient attributed to someone else.
_THIRD_PARTY = re.compile(
    r"\b(my\s+(mother|father|wife|husband|son|daughter|sister|brother|friend|"
    r"amma|thatha|child|parent)|he\s+|she\s+|they\s+have)\b", re.I)


def _expiry(kind: str) -> datetime | None:
    days = _TTL_DAYS.get(kind)
    return datetime.now(timezone.utc) + timedelta(days=days) if days else None


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
def recall(db: Session, patient_user_id: str, *, limit: int = _MAX_FACTS_IN_PROMPT) -> list[PatientMemory]:
    """Live facts for this patient, most confident first."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(PatientMemory)
        .where(PatientMemory.patient_user_id == patient_user_id)
        .order_by(PatientMemory.confidence.desc(), PatientMemory.updated_at.desc())
        .limit(limit * 2)
    ).scalars().all()

    return [
        row for row in rows
        if row.expires_at is None or row.expires_at > now
    ][:limit]


def merge_clinical(
    recorded: list | None, memories: list[PatientMemory], key: str
) -> list[str]:
    """Combine a profile list with what the patient told us in conversation.

    The recorded profile comes first and is never removed — it was entered
    deliberately, and a conversational aside should not be able to delete a
    clinician-entered allergy. Remembered values are appended when they are
    genuinely new, compared case-insensitively so "Diabetes" and "diabetes"
    do not both appear.
    """
    combined = [str(item).strip() for item in (recorded or []) if str(item).strip()]
    seen = {item.lower() for item in combined}
    for entry in memories:
        if entry.key != key:
            continue
        for value in str(entry.value or "").split(","):
            value = value.strip()
            if value and value.lower() not in seen and value.lower() != "yes":
                combined.append(value)
                seen.add(value.lower())
    return combined


def as_context(memories: list[PatientMemory]) -> str:
    """Render for a prompt. Contains no identifiers by construction.

    Framed as "previously mentioned" rather than as fact, because that is what
    it is — the model should treat it as something to confirm, not assert.
    """
    if not memories:
        return ""
    lines = [
        f"- {m.key.replace('_', ' ')}: {m.value}"
        for m in memories
    ]
    return (
        "Things this patient mentioned in earlier conversations (treat as "
        "context to confirm, not as confirmed fact):\n" + "\n".join(lines)
    )


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
def remember(
    db: Session,
    patient_user_id: str,
    *,
    key: str,
    value: str,
    confidence: float = 0.6,
    session_id: str | None = None,
    quote: str | None = None,
) -> PatientMemory | None:
    """Record or update one fact. Unknown keys are refused."""
    kind = ALLOWED_KEYS.get(key)
    if kind is None:
        logger.debug("Refused unknown memory key %r", key)
        return None

    value = (value or "").strip()[:400]
    if not value:
        return None

    existing = db.execute(
        select(PatientMemory).where(
            PatientMemory.patient_user_id == patient_user_id,
            PatientMemory.key == key,
        )
    ).scalar_one_or_none()

    if existing is not None:
        # A repeated statement raises confidence; a changed one replaces the
        # value and resets it, because the patient just corrected us.
        if existing.value.lower() == value.lower():
            existing.confidence = min(1.0, existing.confidence + 0.15)
        elif key in _MULTI_VALUED:
            # An addition, not a correction. Someone mentioning a second
            # allergy has not withdrawn the first one.
            merged = _merge_values(existing.value, value)
            if merged.lower() != existing.value.lower():
                existing.value = merged
                existing.confidence = max(existing.confidence, confidence)
        else:
            existing.value = value
            existing.confidence = confidence
        existing.source_session_id = session_id or existing.source_session_id
        existing.source_quote = quote or existing.source_quote
        existing.expires_at = _expiry(kind)
        db.commit()
        return existing

    memory = PatientMemory(
        patient_user_id=patient_user_id,
        kind=kind,
        key=key,
        value=value,
        confidence=confidence,
        source_session_id=session_id,
        source_quote=(quote or "")[:500] or None,
        expires_at=_expiry(kind),
    )
    db.add(memory)
    db.commit()
    return memory


def forget(db: Session, patient_user_id: str, memory_id: str) -> bool:
    memory = db.execute(
        select(PatientMemory).where(
            PatientMemory.id == memory_id,
            PatientMemory.patient_user_id == patient_user_id,
        )
    ).scalar_one_or_none()
    if memory is None:
        return False
    db.delete(memory)
    db.commit()
    return True


def forget_all(db: Session, patient_user_id: str) -> int:
    rows = db.execute(
        select(PatientMemory).where(PatientMemory.patient_user_id == patient_user_id)
    ).scalars().all()
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
_EXTRACT_SYSTEM = """You extract durable facts a health assistant should
remember about a patient, from something the patient just said.

Only extract a fact if ALL of these hold:
- The patient said it about THEMSELVES, not about a relative or friend.
- It will still be true in a month.
- It is one of the allowed keys.

Allowed keys: allergy, chronic_condition, past_procedure, regular_medication,
pregnancy, caring_for, lives_alone, occupation, transport_difficulty,
preferred_language, prefers_teleconsultation, preferred_area

Do NOT extract: current symptoms, how they feel today, pain scores, durations,
anything you inferred rather than were told, or anything about another person.

Return JSON only. An empty list is the correct answer most of the time.
{"facts": [{"key": "...", "value": "...", "confidence": 0.0-1.0}]}"""


def extract(text: str) -> list[dict]:
    """Find durable facts in one patient utterance.

    Deterministic patterns first — they are more reliable than a model for the
    handful of things that have a fixed shape. The model then runs only if the
    utterance is substantial enough to plausibly contain something else.
    """
    content = (text or "").strip()
    if len(content) < 12:
        return []

    facts: list[dict] = []
    seen: set[str] = set()

    for key, pattern in _PATTERNS:
        match = pattern.search(content)
        if not match:
            continue
        # Guard against "my mother is diabetic".
        window = content[max(0, match.start() - 40): match.end()]
        if _THIRD_PARTY.search(window):
            continue
        captured = next((g for g in (match.groups() or ()) if g), None)
        value = _clean_value(captured) if captured else "yes"
        if not value:
            continue
        if key in seen:
            continue
        seen.add(key)
        facts.append({"key": key, "value": value, "confidence": 0.9, "source": "pattern"})

    if len(content) < 40:
        return facts

    data, completion = llm.complete_json(
        f'Patient said: "{content[:600]}"', system=_EXTRACT_SYSTEM,
        temperature=0.0, max_tokens=200, fast=True,
    )
    if not completion or not data:
        return facts

    for entry in (data.get("facts") or [])[:4]:
        key = str(entry.get("key", "")).strip()
        value = str(entry.get("value", "")).strip()
        if key not in ALLOWED_KEYS or not value or key in seen:
            continue
        if _THIRD_PARTY.search(value):
            continue
        seen.add(key)
        facts.append({
            "key": key,
            "value": value,
            # Model-extracted facts start lower than pattern-matched ones and
            # have to be repeated before they are treated as settled.
            "confidence": min(0.75, float(entry.get("confidence", 0.5))),
            "source": "model",
        })

    return facts


def learn_from_turn(
    db: Session,
    patient_user_id: str,
    *,
    text: str,
    session_id: str | None = None,
    confidential: bool = False,
) -> list[str]:
    """Extract and store. Returns the keys learned. Never raises."""
    if confidential:
        # A private conversation leaves nothing behind, including memory.
        return []
    try:
        learned = []
        for fact in extract(text):
            memory = remember(
                db, patient_user_id,
                key=fact["key"], value=fact["value"],
                confidence=fact["confidence"],
                session_id=session_id, quote=text[:500],
            )
            if memory is not None:
                learned.append(fact["key"])
        return learned
    except Exception:  # noqa: BLE001
        logger.warning("Memory extraction failed", exc_info=True)
        return []
