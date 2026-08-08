"""Doctor-style consultation: take a history, then reason backwards.

The failure of a typical symptom checker is that it treats the first sentence
as the whole story. Real patients open with "I have a headache" — which is
compatible with dehydration and with a subarachnoid haemorrhage. A clinician
does not answer that sentence; they ask three or four targeted questions
first, and each question is chosen to *discriminate* between the explanations
still in play.

That is what this module does:

    accumulate → red-flag screen → what's missing? → ASK one question
                                 → enough to say something? → ASSESS

**Backward reasoning.** Rather than asking a fixed script in order, the
next question is chosen from the candidate explanations: the highest-yield
question is the one whose answer would most change which explanation fits.
"Does the pain wake you at night?" earns its place because the answer moves
probability; "how are you feeling generally?" does not.

**Two hard limits, both deliberate.**

*Urgency is not negotiable by conversation.* The red-flag engine runs over the
accumulated transcript on every turn. If it fires, question-asking stops
immediately and the escalation is delivered. A patient who mentions crushing
chest pain on turn one is not asked three more questions first.

*The assessment is explicitly uncertain.* SuwaPath is not examining anyone and
has no test results. The output says what fits, what would settle it, and who
should confirm — it never lands on a single answer and call it a diagnosis.
That is not hedging for legal cover; an assistant that sounds certain is one
patients stop verifying.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import UrgencyLevel
from app.services import llm, navigation
from app.services import red_flag_engine as rfe

logger = logging.getLogger(__name__)

# After this many questions, answer with what we have. A chat that keeps
# interrogating is worse than one that commits to a careful, caveated view —
# patients abandon it and get nothing.
MAX_QUESTIONS = 4

# Below this many filled slots we should still be asking, unless the patient
# has told us to get on with it.
MIN_SLOTS_FOR_ASSESSMENT = 3


# --------------------------------------------------------------------------
# History slots
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Slot:
    key: str
    label: str
    # Deterministic fallback question, used when no model is available.
    question: str
    # Evidence that the patient has already covered this ground.
    detector: re.Pattern


_SLOTS: tuple[Slot, ...] = (
    Slot(
        "onset", "when it started",
        "When did this start, and did it come on suddenly or build up gradually?",
        re.compile(
            r"\b(since|for|started|began|ago|yesterday|today|this morning|"
            r"last night|sudden|gradual|slowly|\d+\s*(day|week|month|hour|year))\b",
            re.I),
    ),
    Slot(
        "character", "what it feels like",
        "How would you describe it — sharp, dull, burning, throbbing, or "
        "something else?",
        re.compile(
            r"\b(sharp|dull|burning|throbbing|stabbing|crushing|cramp|ache|"
            r"aching|tight|pressure|heavy|shooting|tingl|numb|itch)\w*\b", re.I),
    ),
    Slot(
        "severity", "how bad it is",
        "On a scale of 0 to 10, how bad is it at its worst? And is it stopping "
        "you doing normal things?",
        re.compile(
            r"\b(\d{1,2}\s*(/|out of)\s*10|mild|moderate|severe|unbearable|"
            r"worst|excruciating|can'?t (sleep|walk|work|move|stand))\b", re.I),
    ),
    Slot(
        "associated", "other symptoms",
        "Is anything else happening alongside it — fever, vomiting, "
        "breathlessness, rash, or feeling faint?",
        re.compile(
            r"\b(also|along with|too|as well|fever|vomit|nausea|rash|cough|"
            r"breathless|short of breath|dizzy|faint|chills|sweat|swelling|"
            r"diarrh|bleeding|weight loss|no other)\w*\b", re.I),
    ),
    Slot(
        "pattern", "when it is worse",
        "Is it there all the time, or does it come and go? Is there anything "
        "that makes it better or worse?",
        re.compile(
            r"\b(constant|all the time|comes and goes|intermittent|worse (when|"
            r"at|after|in)|better (when|after|with)|at night|morning|after eating|"
            r"on movement|when i)\b", re.I),
    ),
    Slot(
        "history", "your background",
        "Have you had this before, and are you being treated for anything "
        "long-term such as diabetes, blood pressure or asthma?",
        re.compile(
            r"\b(before|previously|history|diabet|blood pressure|hypertens|"
            r"asthma|heart|kidney|thyroid|cholesterol|first time|never had|"
            r"taking .*(tablet|medicine)|on medication)\w*\b", re.I),
    ),
)

_SLOT_BY_KEY = {slot.key: slot for slot in _SLOTS}

# The patient signalling they want an answer now, not more questions.
_IMPATIENT = re.compile(
    r"\b(just tell me|stop asking|answer me|what do you think|"
    r"i (already )?(told|said)|get to the point|hurry|no more questions)\b", re.I)

# A question rather than a symptom report — should not start a consultation.
_INFORMATIONAL = re.compile(
    r"^\s*(what|why|how|when|which|who|is|are|does|do|can|should)\b.{0,80}\?*\s*$",
    re.I)


@dataclass
class ConsultState:
    """Everything the consultation knows, rebuilt from the transcript."""

    chief_complaint: str = ""
    transcript: str = ""
    filled: set[str] = field(default_factory=set)
    asked: list[str] = field(default_factory=list)
    questions_asked: int = 0
    impatient: bool = False

    @property
    def missing(self) -> list[Slot]:
        return [s for s in _SLOTS if s.key not in self.filled]

    @property
    def coverage(self) -> float:
        return round(len(self.filled) / len(_SLOTS), 2)


@dataclass
class ConsultTurn:
    """One consultation step: either a question or an assessment."""

    mode: str                       # ask | assess | escalate
    answer: str                     # markdown, shown to the patient
    question: str | None = None
    slot: str | None = None
    urgency: str = "routine"
    specialty: str | None = None
    specialty_name: str | None = None
    differentials: list[dict] = field(default_factory=list)
    tests: list[dict] = field(default_factory=list)
    coverage: float = 0.0
    source: str = "deterministic"   # which path produced the wording
    latency_ms: int = 0
    red_flags: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# State reconstruction
# --------------------------------------------------------------------------
def build_state(messages: list[dict], *, memory_asked: list[str] | None = None) -> ConsultState:
    """Derive the consultation state from the conversation so far.

    Deriving rather than storing means a resumed or replayed conversation
    always produces the same state — there is no separate progress record to
    drift out of sync with what was actually said.
    """
    patient_turns = [m["content"] for m in messages if m.get("role") == "user"]
    assistant_turns = [m["content"] for m in messages if m.get("role") == "assistant"]
    transcript = "\n".join(patient_turns)

    state = ConsultState(
        chief_complaint=patient_turns[0].strip() if patient_turns else "",
        transcript=transcript,
        asked=list(memory_asked or []),
        # Only assistant turns that actually ended in a question count.
        questions_asked=sum(1 for t in assistant_turns if t.rstrip().endswith("?")),
        impatient=bool(patient_turns and _IMPATIENT.search(patient_turns[-1])),
    )

    for slot in _SLOTS:
        if slot.detector.search(transcript):
            state.filled.add(slot.key)

    return state


def should_consult(text: str, *, has_history: bool) -> bool:
    """Is this a symptom conversation, or just a question?

    "What does low haemoglobin mean?" is answered directly. "My chest hurts"
    starts a history.
    """
    if has_history:
        return True
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _INFORMATIONAL.match(stripped):
        return False
    return True


# --------------------------------------------------------------------------
# The consultation step
# --------------------------------------------------------------------------
def run(
    *,
    messages: list[dict],
    patient_context: dict,
    language: str = "en",
    memory_asked: list[str] | None = None,
) -> ConsultTurn:
    """Advance the consultation by one turn."""
    started = time.perf_counter()
    state = build_state(messages, memory_asked=memory_asked)

    # 1. Deterministic red-flag screen over everything said so far. This runs
    #    first on every turn and can end the consultation immediately.
    context = rfe.build_context(
        age=patient_context.get("age"),
        sex=patient_context.get("sex"),
        is_pregnant=bool(patient_context.get("is_pregnant")),
        is_postpartum=bool(patient_context.get("is_postpartum")),
        pregnancy_week=patient_context.get("pregnancy_week"),
        chronic_conditions=patient_context.get("chronic_conditions") or [],
    )
    red_flags = rfe.assess_text(state.transcript, context)
    recommendation = navigation.navigate(
        red_flags=red_flags, chief_complaint=state.chief_complaint
    )

    red_flag_payload = {
        "urgency": str(red_flags.urgency),
        "rules": red_flags.rules_as_dicts(),
        "requires_emergency_facility": red_flags.requires_emergency_facility,
        "escalation_message": red_flags.escalation_message,
    }

    # 2. An emergency ends question-asking. Nothing the patient could say next
    #    would make it safe to keep interviewing them.
    if red_flags.urgency == UrgencyLevel.EMERGENCY:
        return ConsultTurn(
            mode="escalate",
            answer=_escalation_markdown(red_flags, recommendation),
            urgency=str(red_flags.urgency),
            specialty=recommendation.specialty_code,
            specialty_name=recommendation.specialty_name,
            tests=recommendation.recommended_tests,
            coverage=state.coverage,
            source="red_flag_engine",
            latency_ms=int((time.perf_counter() - started) * 1000),
            red_flags=red_flag_payload,
        )

    # 3. Still gathering?
    enough = (
        len(state.filled) >= MIN_SLOTS_FOR_ASSESSMENT
        or state.questions_asked >= MAX_QUESTIONS
        or state.impatient
        or not state.missing
    )

    if not enough:
        turn = _ask(state, patient_context, language)
        turn.urgency = str(red_flags.urgency)
        turn.coverage = state.coverage
        turn.red_flags = red_flag_payload
        turn.latency_ms = int((time.perf_counter() - started) * 1000)
        return turn

    # 4. Assess.
    turn = _assess(state, patient_context, red_flags, recommendation, language)
    turn.coverage = state.coverage
    turn.red_flags = red_flag_payload
    turn.latency_ms = int((time.perf_counter() - started) * 1000)
    return turn


# --------------------------------------------------------------------------
# Asking
# --------------------------------------------------------------------------
_ASK_SYSTEM = """You are an experienced Sri Lankan physician taking a history.

You ask ONE question at a time — the single question whose answer would most
change what you think is going on. Choose it by reasoning backwards: given
what has been said, which explanations are still in play, and what would best
tell them apart?

Rules:
- Exactly one question. Never a list, never numbered options.
- Plain conversational language a worried person understands. No jargon.
- Never re-ask something already answered.
- Do not diagnose, reassure, or explain anything yet. Just ask.
- At most 35 words.
- Output the question only. No preamble, no quotes."""


def _ask(state: ConsultState, patient_context: dict, language: str) -> ConsultTurn:
    """Choose and phrase the next history question."""
    candidates = [s for s in state.missing if s.key not in state.asked] or state.missing
    target = candidates[0]

    language_name = {"en": "English", "si": "Sinhala", "ta": "Tamil"}.get(language, "English")
    known = ", ".join(sorted(state.filled)) or "nothing yet"

    prompt = f"""Patient's words so far:
\"\"\"{state.transcript[:1500]}\"\"\"

Background: {_context_line(patient_context)}
Already covered: {known}
Still unknown: {", ".join(s.label for s in state.missing)}
Question number {state.questions_asked + 1} of at most {MAX_QUESTIONS}.

The area most worth asking about next is: {target.label}.
Ask your one question in {language_name}."""

    completion = llm.complete(
        prompt, system=_ASK_SYSTEM, temperature=0.4, max_tokens=90, fast=True
    )

    question = _clean_question(completion.text) if completion else ""
    if not question:
        question = target.question

    return ConsultTurn(
        mode="ask",
        answer=question,
        question=question,
        slot=target.key,
        source=completion.provider if completion else "deterministic",
    )


def _clean_question(text: str) -> str:
    """Models like to add a preamble and ask three things. Take one question."""
    cleaned = (text or "").strip().strip('"').strip()
    cleaned = re.sub(r"^(sure|okay|ok|alright|thanks|i see)[,.!:]?\s*", "", cleaned, flags=re.I)
    # Keep only the first question, including its mark.
    match = re.search(r"^(.*?\?)", cleaned, re.S)
    if match:
        cleaned = match.group(1)
    cleaned = " ".join(cleaned.split())
    return cleaned if len(cleaned) <= 300 else ""


# --------------------------------------------------------------------------
# Assessing
# --------------------------------------------------------------------------
_ASSESS_SYSTEM = """You are an experienced Sri Lankan physician writing to the
patient after taking their history. You are helping them decide what to do
next — you are NOT diagnosing them.

Write markdown in exactly this shape:

**What I'm hearing**
One short paragraph reflecting their story back accurately.

**What could explain it**
2-3 bullets. Each names a possibility and says in plain words what in their
story fits it, and what argues against it. Order by how well it fits.

**What would actually settle it**
1-3 bullets naming specific tests or examinations and what each one rules in
or out.

**What I'd do**
Which kind of doctor, how soon, and why. Then what to watch for that would
mean going sooner.

Hard rules:
- Never state a diagnosis as fact. Say "this would fit", "this is worth
  excluding".
- Never name a medicine or a dose.
- Never contradict the urgency you are given — it is fixed.
- Use **bold** for the section headings exactly as above, and keep paragraphs
  short. No headings other than those four.
- Under 320 words. Sri Lankan context (government OPD, channelling centres).
- End with one sentence in italics saying plainly that you cannot be certain
  without an examination and that a doctor should confirm."""


def _assess(
    state: ConsultState,
    patient_context: dict,
    red_flags: rfe.RedFlagResult,
    recommendation: navigation.NavigationResult,
    language: str,
) -> ConsultTurn:
    language_name = {"en": "English", "si": "Sinhala", "ta": "Tamil"}.get(language, "English")

    tests = recommendation.recommended_tests or []
    test_line = ", ".join(t.get("name", "") for t in tests) or "none suggested by the rules"
    flags = ", ".join(r.label for r in red_flags.triggered_rules) or "none triggered"

    prompt = f"""Patient's history in their own words:
\"\"\"{state.transcript[:2000]}\"\"\"

Background: {_context_line(patient_context)}

Fixed findings from SuwaPath's clinical rule engine — you must not contradict these:
- Urgency: {red_flags.urgency}
- Rules triggered: {flags}
- Suggested specialty: {recommendation.specialty_name}
- Tests the engine suggests: {test_line}
- Reason: {recommendation.reason}

Write your reply in {language_name}."""

    completion = llm.complete(
        prompt, system=_ASSESS_SYSTEM, temperature=0.35, max_tokens=1000
    )

    if completion:
        answer = completion.text.strip()
        source = completion.provider
    else:
        answer = _assessment_markdown(state, red_flags, recommendation)
        source = "deterministic"

    return ConsultTurn(
        mode="assess",
        answer=answer,
        urgency=str(red_flags.urgency),
        specialty=recommendation.specialty_code,
        specialty_name=recommendation.specialty_name,
        tests=tests,
        source=source,
    )


def _context_line(patient_context: dict) -> str:
    """A compact, de-identified background line."""
    bits = []
    if patient_context.get("age_band"):
        bits.append(str(patient_context["age_band"]).replace("_", " "))
    elif patient_context.get("age"):
        bits.append(f"{patient_context['age']} years old")
    if patient_context.get("sex"):
        bits.append(str(patient_context["sex"]))
    if patient_context.get("is_pregnant"):
        week = patient_context.get("pregnancy_week")
        bits.append(f"pregnant{f' ({week} weeks)' if week else ''}")
    chronic = patient_context.get("chronic_conditions") or []
    if chronic:
        bits.append("has " + ", ".join(str(c) for c in chronic[:4]))
    allergies = patient_context.get("allergies") or []
    if allergies:
        bits.append("allergic to " + ", ".join(str(a) for a in allergies[:3]))
    return "; ".join(bits) or "no background on file"


# --------------------------------------------------------------------------
# Deterministic composers
# --------------------------------------------------------------------------
# These are not placeholders. With no model reachable they are what the
# patient reads, so they are written to be genuinely usable — structured,
# specific, and honest about their own limits.
_URGENCY_GUIDANCE = {
    "emergency": ("**Go to an emergency department now.**", "immediately"),
    "urgent": ("**See a doctor today or tomorrow.**", "within 24-48 hours"),
    "soon": ("**Get this looked at within the next few days.**", "within a few days"),
    "routine": ("**This can be seen at a normal appointment.**", "when convenient"),
}


def _escalation_markdown(
    red_flags: rfe.RedFlagResult, recommendation: navigation.NavigationResult
) -> str:
    reasons = "\n".join(
        f"- {rule.label} — {rule.rationale}" for rule in red_flags.triggered_rules[:4]
    )
    lines = [
        "**This needs emergency care now — please don't wait.**",
        "",
        red_flags.escalation_message
        or "What you've described matches a pattern that needs to be assessed "
           "straight away.",
        "",
        "**Why I'm saying that**",
        reasons or "- Your description matched an emergency rule.",
        "",
        "**What to do**",
        "- Call **1990** (Suwa Seriya ambulance, free) or go to the nearest "
        "emergency department",
        "- Take someone with you if you can, and don't drive yourself",
        "- Bring any medicines you take and any recent reports",
    ]
    if recommendation.required_capabilities:
        lines += [
            "",
            "**The facility should have:** "
            + ", ".join(c.replace("_", " ") for c in recommendation.required_capabilities[:5]),
        ]
    lines += [
        "",
        "_I'd rather send you and be wrong than the other way round. A doctor "
        "there will assess you properly._",
    ]
    return "\n".join(lines)


def _assessment_markdown(
    state: ConsultState,
    red_flags: rfe.RedFlagResult,
    recommendation: navigation.NavigationResult,
) -> str:
    """Assessment written without a model, from the engines' own output."""
    urgency_line, timing = _URGENCY_GUIDANCE.get(
        str(red_flags.urgency), _URGENCY_GUIDANCE["routine"]
    )

    heard = state.chief_complaint.strip() or "what you've described"
    if len(heard) > 200:
        heard = heard[:200].rsplit(" ", 1)[0] + "…"

    lines = [
        "**What I'm hearing**",
        f"You've told me about {heard.lower().rstrip('.')}. "
        + (
            "Based on the patterns in what you've said, here's where I'd point you."
            if red_flags.triggered_rules
            else "Nothing you've described matches an emergency pattern, which is "
                 "reassuring as far as it goes."
        ),
        "",
        "**What could explain it**",
    ]

    if red_flags.triggered_rules:
        for rule in red_flags.triggered_rules[:3]:
            lines.append(f"- **{rule.label}** — {rule.rationale}")
    else:
        lines.append(
            "- Without an examination I can't narrow this down responsibly. "
            "What you've described is common to several ordinary causes as well "
            "as a few that are worth excluding."
        )

    lines += ["", "**What would actually settle it**"]
    if recommendation.recommended_tests:
        for test in recommendation.recommended_tests[:3]:
            name = test.get("name", "a test")
            why = test.get("reason") or test.get("rationale") or "helps narrow the cause"
            lines.append(f"- **{name}** — {why}")
    else:
        lines.append(
            "- An examination by a doctor, who can check the things I can't see "
            "from text"
        )

    lines += [
        "",
        "**What I'd do**",
        urgency_line,
        f"- See **{recommendation.specialty_name}** — {recommendation.reason}",
        f"- Timing: {timing}",
        f"- {recommendation.suggested_next_action}",
    ]

    if recommendation.patient_guidance:
        lines.append(f"- {recommendation.patient_guidance}")

    lines += [
        "",
        "**Go sooner if** the pain becomes severe, you develop a high fever, "
        "you have trouble breathing, you start vomiting repeatedly, or you feel "
        "faint.",
        "",
        "_I can't be certain without examining you — treat this as a starting "
        "point and let a doctor confirm it._",
    ]
    return "\n".join(lines)


def status() -> dict:
    return {
        "slots": [s.key for s in _SLOTS],
        "max_questions": MAX_QUESTIONS,
        "min_slots_for_assessment": MIN_SLOTS_FOR_ASSESSMENT,
        "urgency_authority": "deterministic_red_flag_engine",
        "escalation_short_circuits_questions": True,
    }
