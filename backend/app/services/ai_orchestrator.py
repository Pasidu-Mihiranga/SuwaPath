"""Gemini-based AI orchestration.

Gemini decides which specialised capability a turn requires and performs the
language work: asking natural follow-up questions, extracting structure from a
conversation, and explaining findings in plain language.

What Gemini never does:
  * decide urgency — that is the deterministic red-flag engine (rule 1)
  * rank providers — that is the matching engine
  * overwrite the patient's original words — raw turns are stored separately

Every LLM call has a deterministic fallback, so the product is fully functional
with no API key configured.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.config import settings
from app.clinical.lexicon import concept_label, extract_concepts
from app.models.enums import Language
from app.services import llm
from app.services.knowledge import knowledge_service

logger = logging.getLogger(__name__)


class Capability(StrEnum):
    """Specialised capabilities the orchestrator can route a request to."""

    SYMPTOM_INTAKE = "symptom_intake"
    DOCUMENT_UNDERSTANDING = "document_understanding"
    IMAGE_SCREENING = "image_screening"
    PROVIDER_MATCHING = "provider_matching"
    FACILITY_MATCHING = "facility_matching"
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"
    WEB_SEARCH = "web_search"


LANGUAGE_NAMES = {
    Language.EN: "English",
    Language.SI: "Sinhala",
    Language.TA: "Tamil",
}

# Sinhala and Tamil each sit in their own Unicode block, so a per-character
# range check identifies them exactly — no model call or library needed.
_SINHALA_RANGE = (0x0D80, 0x0DFF)
_TAMIL_RANGE = (0x0B80, 0x0BFF)


def detect_language(text: str) -> Language | None:
    """Guess the script a message is written in, for per-turn replies.

    Returns ``None`` when the text has no alphabetic signal either way (pure
    digits, punctuation, emoji) — e.g. "7" answering a severity question —
    so the caller can keep whatever language the conversation was already in
    instead of snapping back to English.
    """
    has_latin_letter = False
    for ch in text:
        codepoint = ord(ch)
        if _SINHALA_RANGE[0] <= codepoint <= _SINHALA_RANGE[1]:
            return Language.SI
        if _TAMIL_RANGE[0] <= codepoint <= _TAMIL_RANGE[1]:
            return Language.TA
        if ch.isascii() and ch.isalpha():
            has_latin_letter = True
    return Language.EN if has_latin_letter else None

SAFETY_PREAMBLE = """You are SuwaPath's clinical intake assistant for Sri Lanka.

Absolute rules:
- You are a care-navigation and intake assistant, NOT a diagnostician. Never
  state a diagnosis as certain and never tell a patient they definitely have a
  named condition.
- Never decide or state an urgency level. A separate deterministic clinical
  engine decides urgency. Do not tell the patient to relax or that something is
  not serious.
- Never invent test results, doctor names, hospital names or prices.
- Keep language simple, calm and respectful. Short sentences.
- If the patient mentions self-harm, respond with warmth and encourage
  immediate professional support.
"""


@dataclass
class OrchestrationDecision:
    capability: Capability
    rationale: str
    parameters: dict[str, Any] = field(default_factory=dict)
    source: str = "rule_based"


@dataclass
class AssistantTurn:
    """One assistant reply plus what the orchestrator learned from the turn."""

    message: str
    capability: Capability
    is_complete: bool = False
    asked_about: list[str] = field(default_factory=list)
    source: str = "rule_based"
    citations: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------
# Language model access
# --------------------------------------------------------------------------
# All generation goes through app.services.llm, which tries Groq, then
# OpenRouter, then Gemini, and reports which one answered. The helpers below
# are the legacy single-string interface every existing call site uses; they
# stay so those call sites did not all have to change at once.
def gemini_available() -> bool:
    """Deprecated name kept for call-site compatibility: is *any* model up?"""
    return llm.available()


def _generate(prompt: str, *, temperature: float = 0.2, as_json: bool = False) -> str | None:
    """Single-prompt generation. Returns None when no provider answered."""
    completion = llm.complete(prompt, temperature=temperature, as_json=as_json)
    return completion.text if completion else None


def _parse_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Models occasionally wrap JSON in prose or code fences.
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


# --------------------------------------------------------------------------
# Capability routing
# --------------------------------------------------------------------------
ROUTING_KEYWORDS: list[tuple[Capability, tuple[str, ...]]] = [
    (Capability.PROVIDER_MATCHING,
     ("find a doctor", "which doctor", "book a doctor", "specialist near",
      "doctor near me", "recommend a doctor", "see a specialist")),
    (Capability.FACILITY_MATCHING,
     ("which hospital", "hospital near", "find a hospital", "where can i get",
      "lab near", "diagnostic centre", "testing centre", "where to test")),
    (Capability.DOCUMENT_UNDERSTANDING,
     ("my report", "lab report", "blood report", "test result", "uploaded report",
      "explain my report", "prescription")),
    (Capability.IMAGE_SCREENING,
     ("x-ray", "xray", "scan image", "my scan", "chest image", "uploaded image")),
    (Capability.WEB_SEARCH,
     ("latest news", "current outbreak", "dengue cases this", "new guidelines",
      "recent update", "this week in")),
    (Capability.KNOWLEDGE_RETRIEVAL,
     ("what is", "what does", "why does", "how does", "is it normal",
      "tell me about", "explain")),
]


def route(user_text: str, *, has_attachment: str | None = None) -> OrchestrationDecision:
    """Choose the capability for a turn.

    Attachments route deterministically. Otherwise Gemini classifies, with a
    keyword router as fallback.
    """
    if has_attachment == "document":
        return OrchestrationDecision(
            Capability.DOCUMENT_UNDERSTANDING,
            "A medical document was uploaded, so the document pipeline handles it.",
            source="deterministic",
        )
    if has_attachment == "image":
        return OrchestrationDecision(
            Capability.IMAGE_SCREENING,
            "A medical image was uploaded, so the computer-vision pipeline handles it.",
            source="deterministic",
        )

    decision = _route_with_gemini(user_text)
    if decision:
        return decision

    lowered = (user_text or "").lower()
    for capability, keywords in ROUTING_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return OrchestrationDecision(
                capability,
                f"Matched an explicit request pattern for {capability.value.replace('_', ' ')}.",
            )

    return OrchestrationDecision(
        Capability.SYMPTOM_INTAKE,
        "The message describes a health concern, so it is handled as symptom intake.",
    )


def _route_with_gemini(user_text: str) -> OrchestrationDecision | None:
    if not user_text.strip():
        return None
    prompt = f"""{SAFETY_PREAMBLE}

Classify the user's message into exactly one capability:

- symptom_intake: describing symptoms or a health concern
- knowledge_retrieval: asking a general health/medical knowledge question
- provider_matching: asking to find or choose a doctor
- facility_matching: asking to find a hospital, lab or diagnostic centre
- document_understanding: asking about a medical report or prescription
- image_screening: asking about a medical image or scan
- web_search: asking about current public information (outbreaks, news,
  newly published guidance) where recent external data is genuinely required

User message: "{user_text}"

Return JSON only: {{"capability": "...", "rationale": "one short sentence"}}"""

    data = _parse_json(_generate(prompt, temperature=0.0, as_json=True))
    if not data:
        return None
    try:
        capability = Capability(data.get("capability", ""))
    except ValueError:
        return None
    return OrchestrationDecision(
        capability,
        str(data.get("rationale", "Classified by the AI orchestrator.")),
        source="gemini",
    )


# --------------------------------------------------------------------------
# Symptom conversation
# --------------------------------------------------------------------------
# Ordered follow-up topics. The deterministic path walks these; Gemini is asked
# to cover the same ground in natural language.
FOLLOW_UP_TOPICS: list[tuple[str, str]] = [
    ("onset", "When did this start, and did it come on suddenly or gradually?"),
    ("severity", "How severe is it right now on a scale of 1 to 10?"),
    ("character", "Can you describe what it feels like, and whether anything makes it better or worse?"),
    ("associated", "Are you having any other symptoms alongside this?"),
    ("history", "Do you have any existing medical conditions we should know about?"),
    ("medication", "Are you taking any medicines at the moment, including anything over the counter?"),
    ("allergies", "Do you have any known allergies?"),
]

# Concept-specific probes that are clinically more valuable than generic ones.
TARGETED_PROBES: dict[str, list[tuple[str, str]]] = {
    "chest_pain": [
        ("radiation", "Does the pain spread to your arm, jaw, neck or back?"),
        ("breathing", "Are you also short of breath?"),
        ("sweating", "Have you been sweating or feeling clammy?"),
    ],
    "shortness_of_breath": [
        ("exertion", "Does it happen at rest, or only when you exert yourself?"),
        ("swelling", "Have you noticed any swelling in your legs or ankles?"),
    ],
    "severe_headache": [
        ("sudden", "Did the headache reach its worst point within seconds or minutes?"),
        ("vision", "Any change in your vision, or sensitivity to light?"),
        ("neck", "Is your neck stiff, or do you have a fever?"),
    ],
    "skin_lesion": [
        ("duration", "How long has it been there, and has it changed in size or colour?"),
        ("bleeding", "Does it bleed, itch or fail to heal?"),
    ],
    "abdominal_pain": [
        ("location", "Where exactly is the pain, and does it move anywhere?"),
        ("bowel", "Any vomiting, or change in your bowel habit?"),
    ],
    "fever": [
        ("duration", "How many days have you had the fever?"),
        ("associated", "Any rash, severe body aches, or bleeding from the gums or nose?"),
    ],
    "vaginal_bleeding": [
        ("amount", "How heavy is the bleeding, and is there any pain with it?"),
    ],
    "genital_symptoms": [
        ("exposure", "When was the possible exposure, and was protection used?"),
        ("testing", "Have you been tested for anything before?"),
    ],
}


def next_assistant_turn(
    *,
    conversation: list[dict],
    language: Language,
    patient_context: dict | None = None,
    max_turns: int = 6,
) -> AssistantTurn:
    """Produce the assistant's next message in a symptom conversation."""
    patient_messages = [m for m in conversation if m["role"] == "patient"]
    assistant_messages = [m for m in conversation if m["role"] == "assistant"]
    combined = " ".join(m["content"] for m in patient_messages)
    concepts, _ = extract_concepts(combined)

    covered = _topics_already_asked(assistant_messages)
    remaining = _remaining_topics(concepts, covered)

    # Enough information gathered, or the conversation has run long enough.
    if len(assistant_messages) >= max_turns or not remaining:
        return AssistantTurn(
            message=_closing_message(language),
            capability=Capability.SYMPTOM_INTAKE,
            is_complete=True,
            source="rule_based",
        )

    turn = _gemini_follow_up(
        conversation=conversation,
        language=language,
        concepts=concepts,
        remaining=remaining,
        patient_context=patient_context or {},
    )
    if turn:
        return turn

    topic, question = remaining[0]
    return AssistantTurn(
        message=_localise(question, language),
        capability=Capability.SYMPTOM_INTAKE,
        asked_about=[topic],
        source="rule_based",
    )


def _topics_already_asked(assistant_messages: list[dict]) -> set[str]:
    covered: set[str] = set()
    for message in assistant_messages:
        covered.update(message.get("meta", {}).get("asked_about", []))
    return covered


def _remaining_topics(concepts: set[str], covered: set[str]) -> list[tuple[str, str]]:
    """Targeted probes for the reported concepts first, then generic topics."""
    ordered: list[tuple[str, str]] = []
    for concept in sorted(concepts):
        for topic, question in TARGETED_PROBES.get(concept, []):
            key = f"{concept}:{topic}"
            if key not in covered:
                ordered.append((key, question))
    for topic, question in FOLLOW_UP_TOPICS:
        if topic not in covered:
            ordered.append((topic, question))
    return ordered


def _gemini_follow_up(
    *,
    conversation: list[dict],
    language: Language,
    concepts: set[str],
    remaining: list[tuple[str, str]],
    patient_context: dict,
) -> AssistantTurn | None:
    transcript = "\n".join(
        f"{'Patient' if m['role'] == 'patient' else 'Assistant'}: {m['content']}"
        for m in conversation
    )
    target_topic, target_question = remaining[0]
    language_name = LANGUAGE_NAMES.get(language, "English")

    context_lines = []
    if patient_context.get("age"):
        context_lines.append(f"Age: {patient_context['age']}")
    if patient_context.get("sex"):
        context_lines.append(f"Sex: {patient_context['sex']}")
    if patient_context.get("is_pregnant"):
        context_lines.append("Currently pregnant")
    if patient_context.get("chronic_conditions"):
        context_lines.append(
            "Known conditions: " + ", ".join(patient_context["chronic_conditions"])
        )

    prompt = f"""{SAFETY_PREAMBLE}

You are collecting a structured medical history through conversation.

Known patient context:
{chr(10).join(context_lines) or "None recorded"}

Symptoms detected so far: {', '.join(sorted(concept_label(c) for c in concepts)) or 'none yet'}

Conversation so far:
{transcript}

The next clinically useful thing to ask about is: "{target_question}"

Write ONE short, natural follow-up question that covers that ground. Rules:
- Reply in {language_name}.
- Ask only one question.
- Maximum two sentences. Acknowledge what they said briefly if it helps.
- Do not diagnose, do not state urgency, do not give treatment advice.

Return JSON only: {{"question": "..."}}"""

    data = _parse_json(_generate(prompt, temperature=0.3, as_json=True))
    question = (data or {}).get("question")
    if not question or not str(question).strip():
        return None

    return AssistantTurn(
        message=str(question).strip(),
        capability=Capability.SYMPTOM_INTAKE,
        asked_about=[target_topic],
        source="gemini",
    )


def _closing_message(language: Language) -> str:
    return _localise(
        "Thank you. I have enough information to prepare your health summary now.",
        language,
    )


# Minimal built-in translations for the deterministic path. When Gemini is
# configured it writes directly in the patient's language instead.
_TRANSLATIONS: dict[str, dict[Language, str]] = {
    "Thank you. I have enough information to prepare your health summary now.": {
        Language.SI: "ස්තූතියි. ඔබේ සෞඛ්‍ය සාරාංශය සකස් කිරීමට මට දැන් ප්‍රමාණවත් තොරතුරු තිබේ.",
        Language.TA: "நன்றி. உங்கள் சுகாதார சுருக்கத்தைத் தயாரிக்க எனக்கு இப்போது போதுமான தகவல் உள்ளது.",
    },
    "When did this start, and did it come on suddenly or gradually?": {
        Language.SI: "මෙය ආරම්භ වූයේ කවදාද, එය හදිසියේ ද ක්‍රමයෙන් ද ඇති වූයේ?",
        Language.TA: "இது எப்போது தொடங்கியது, திடீரெனவா அல்லது படிப்படியாகவா?",
    },
    "How severe is it right now on a scale of 1 to 10?": {
        Language.SI: "දැන් එය 1 සිට 10 දක්වා පරිමාණයකින් කෙතරම් තදද?",
        Language.TA: "இப்போது 1 முதல் 10 வரையிலான அளவில் இது எவ்வளவு கடுமையானது?",
    },
    "Are you having any other symptoms alongside this?": {
        Language.SI: "මේ සමඟ වෙනත් රෝග ලක්ෂණ තිබේද?",
        Language.TA: "இதனுடன் வேறு ஏதேனும் அறிகுறிகள் உள்ளதா?",
    },
}


def _localise(text: str, language: Language) -> str:
    if language == Language.EN:
        return text
    return _TRANSLATIONS.get(text, {}).get(language, text)


# --------------------------------------------------------------------------
# Structured extraction
# --------------------------------------------------------------------------
def extract_structured_intake(
    *, conversation: list[dict], patient_context: dict | None = None
) -> dict:
    """Convert a raw conversation into the StructuredIntake field set.

    Always returns a usable dict: Gemini output is merged over the
    deterministic baseline, never trusted blindly.
    """
    patient_text = " ".join(m["content"] for m in conversation if m["role"] == "patient")
    baseline = _baseline_extraction(patient_text, patient_context or {})

    extracted = _gemini_extraction(conversation, patient_context or {})
    if not extracted:
        return baseline

    merged = dict(baseline)
    for key in (
        "chief_complaint", "duration_text", "onset", "severity",
        "symptoms", "associated_symptoms", "relevant_history", "medications",
        "allergies", "aggravating_factors", "relieving_factors",
        "negative_findings",
    ):
        value = extracted.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            merged[key] = [str(v)[:160] for v in value][:12]
        elif key == "severity":
            try:
                merged[key] = max(1, min(10, int(value)))
            except (TypeError, ValueError):
                pass
        else:
            merged[key] = str(value)[:400]

    merged["duration_hours"] = _parse_duration_hours(
        merged.get("duration_text") or ""
    ) or baseline.get("duration_hours")
    merged["extraction_source"] = "gemini"
    merged["extraction_confidence"] = 0.85
    return merged


def _baseline_extraction(text: str, patient_context: dict) -> dict:
    concepts, negated = extract_concepts(text)
    symptoms = sorted(concept_label(c) for c in concepts)
    duration_text = _find_duration_text(text)

    return {
        "chief_complaint": _first_sentence(text) or (symptoms[0] if symptoms else "Health concern"),
        "symptoms": symptoms,
        "duration_text": duration_text,
        "duration_hours": _parse_duration_hours(duration_text or ""),
        "severity": _find_severity(text),
        "associated_symptoms": symptoms[1:] if len(symptoms) > 1 else [],
        "relevant_history": list(patient_context.get("chronic_conditions") or []),
        "medications": list(patient_context.get("current_medications") or []),
        "allergies": list(patient_context.get("allergies") or []),
        "potential_red_flags": [],
        "onset": None,
        "aggravating_factors": [],
        "relieving_factors": [],
        "negative_findings": sorted(concept_label(c) for c in negated),
        "extraction_source": "rule_based",
        "extraction_confidence": 0.6,
    }


def _gemini_extraction(conversation: list[dict], patient_context: dict) -> dict | None:
    transcript = "\n".join(
        f"{'Patient' if m['role'] == 'patient' else 'Assistant'}: {m['content']}"
        for m in conversation
    )
    prompt = f"""{SAFETY_PREAMBLE}

Extract structured clinical information from this intake conversation.
Use ONLY what the patient actually said. Do not infer or invent.
If something was not mentioned, use null or an empty list.
Translate any Sinhala or Tamil content into English for these fields.

Known context: {json.dumps(patient_context, default=str)}

Conversation:
{transcript}

Return JSON only with exactly these keys:
{{
  "chief_complaint": "one short phrase",
  "symptoms": ["..."],
  "duration_text": "e.g. 3 weeks",
  "onset": "sudden|gradual|null",
  "severity": 1-10 or null,
  "associated_symptoms": ["..."],
  "relevant_history": ["..."],
  "medications": ["..."],
  "allergies": ["..."],
  "aggravating_factors": ["..."],
  "relieving_factors": ["..."],
  "negative_findings": ["things the patient explicitly denied"]
}}"""
    return _parse_json(_generate(prompt, temperature=0.1, as_json=True))


_DURATION_RE = re.compile(
    r"(\d+|a|an|couple of|few|several)\s*(hour|hr|day|week|month|year)s?", re.IGNORECASE
)
_SEVERITY_RE = re.compile(r"\b(\d{1,2})\s*(?:/|out of)\s*10\b", re.IGNORECASE)

_WORD_NUMBERS = {"a": 1, "an": 1, "couple of": 2, "few": 3, "several": 4}
_UNIT_HOURS = {"hour": 1, "hr": 1, "day": 24, "week": 168, "month": 720, "year": 8760}


def _find_duration_text(text: str) -> str | None:
    match = _DURATION_RE.search(text or "")
    return match.group(0).strip() if match else None


def _parse_duration_hours(text: str) -> float | None:
    match = _DURATION_RE.search(text or "")
    if not match:
        return None
    quantity_raw, unit = match.group(1).lower(), match.group(2).lower()
    quantity = _WORD_NUMBERS.get(quantity_raw)
    if quantity is None:
        try:
            quantity = int(quantity_raw)
        except ValueError:
            return None
    return float(quantity * _UNIT_HOURS.get(unit, 24))


def _find_severity(text: str) -> int | None:
    match = _SEVERITY_RE.search(text or "")
    if match:
        return max(1, min(10, int(match.group(1))))
    lowered = (text or "").lower()
    if any(w in lowered for w in ("unbearable", "worst", "excruciating", "severe")):
        return 8
    if any(w in lowered for w in ("moderate", "quite bad", "uncomfortable")):
        return 5
    if any(w in lowered for w in ("mild", "slight", "a bit", "little")):
        return 2
    return None


def _first_sentence(text: str) -> str | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    parts = re.split(r"[.!?\n]", cleaned)
    return (parts[0].strip()[:200]) if parts and parts[0].strip() else None


# --------------------------------------------------------------------------
# Plain-language explanation (grounded in the knowledge collection)
# --------------------------------------------------------------------------
def explain(
    question: str, *, language: Language = Language.EN, extra_context: str = ""
) -> tuple[str, list[dict]]:
    """Answer a health question, grounded in retrieved knowledge."""
    grounding, citations = knowledge_service.build_context(question, limit=3)

    language_name = LANGUAGE_NAMES.get(language, "English")
    prompt = f"""{SAFETY_PREAMBLE}

Answer the patient's question using ONLY the reference material below plus the
patient context. If the material does not cover it, say what is known and
recommend discussing it with a clinician. Never state a diagnosis.

Reference material:
{grounding or "None available."}

{f"Patient context: {extra_context}" if extra_context else ""}

Question: {question}

Write 3-5 short sentences in {language_name}. End by suggesting the appropriate
next step (which kind of clinician to see, or what to monitor)."""

    answer = _generate(prompt, temperature=0.3)
    if answer:
        return answer, citations

    # Deterministic fallback: summarise the retrieved material directly.
    if grounding:
        results = knowledge_service.search(question, limit=2)
        body = " ".join(r.doc.text.split(". ")[0] + "." for r in results)
        return (
            f"{body} This is general information — please discuss your specific "
            f"situation with a qualified clinician, who can take your full "
            f"history into account.",
            citations,
        )
    return (
        "I do not have reliable information on that specific question. Please "
        "discuss it with a qualified clinician who can assess your individual "
        "situation.",
        [],
    )


# --------------------------------------------------------------------------
# Tavily (current public information only — never for clinical decisions)
# --------------------------------------------------------------------------
def web_search(query: str, *, max_results: int = 4) -> list[dict]:
    """Search current public health information.

    Explicitly not used for emergency or diagnostic decisions (spec §24).
    """
    if not settings.tavily_enabled:
        return []
    try:
        import httpx

        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": True,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
        return [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("content", "")[:400],
            }
            for item in payload.get("results", [])
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily search failed: %s", exc)
        return []


def orchestrator_status() -> dict:
    llm_status = llm.status()
    return {
        "llm": llm_status,
        "llm_available": llm_status["any_available"],
        # Retained so existing clients and tests keep working.
        "gemini_configured": settings.gemini_enabled,
        "gemini_reachable": llm_status["any_available"],
        "gemini_model": settings.gemini_model if settings.gemini_enabled else None,
        "tavily_configured": settings.tavily_enabled,
        "knowledge_backend": knowledge_service.backend,
        "fallback_mode": not llm_status["any_available"],
    }
