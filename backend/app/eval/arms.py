"""The arms under test — four ways to triage the same vignette.

The panel's criticism was "why do you need a multi-agent system, this could be
done easily". That is an empirical claim and it deserves an empirical answer,
so the thing it proposes is built here as a real arm and run on the same data.

    A       deterministic rules, opening message only
    A-full  deterministic rules, opening + every follow-up answer
    B       one LLM call, opening message only          <- the panel's proposal
    C       the live consultation loop: asks, listens, then assesses

The pairs are what carry the argument, not the individual numbers:

    A  vs A-full   what does *information* buy? Both are the same rule engine;
                   only the amount the patient told it differs. This is the
                   honest version of the "only 4 questions" question.
    A-full vs C    what does the *conversation machinery* buy over simply
                   handing the rules everything up front?
    B  vs A/C      LLM judgement versus deterministic rules on identical input.

A caveat that must travel with any table produced from this: the `positive`
vignettes were derived from the rule set, so the deterministic arms have a
structural advantage there and should not be reported as "winning" on them.
The `near_miss`, `benign`, `negation` and `multilingual` categories carry no
such bias and are where the deterministic arms can genuinely be wrong.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from unittest import mock

from app.agent import consult
from app.clinical import hypothesis
from app.clinical.lexicon import extract_concepts
from app.eval.vignettes import Vignette
from app.services import llm, navigation
from app.services import red_flag_engine as rfe

# Ceiling on simulated conversation turns. Well above any current cap, so it
# never truncates a real run — it exists only so a bug cannot spin forever.
MAX_SIMULATED_TURNS = 12

# What the simulated patient says when the assistant asks something the
# vignette has no scripted answer for. Deliberately contains no negation
# marker and no clinical concept: a careless default like "no" would suppress
# concepts in the shared transcript and quietly corrupt every downstream label.
NEUTRAL_ANSWER = "I think it is about the same as usual."


@dataclass
class ArmResult:
    """One arm's outcome on one vignette."""

    vignette_id: str
    arm: str
    urgency: str | None = None
    specialty: str | None = None
    rules: list[str] = field(default_factory=list)
    questions_asked: int = 0
    conversation: list[dict] = field(default_factory=list)
    latency_ms: int = 0
    source: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "vignette_id": self.vignette_id,
            "arm": self.arm,
            "urgency": self.urgency,
            "specialty": self.specialty,
            "rules": self.rules,
            "questions_asked": self.questions_asked,
            "latency_ms": self.latency_ms,
            "source": self.source,
            "error": self.error,
        }


def _context(vignette: Vignette):
    demographics = vignette.demographics
    return rfe.build_context(
        age=demographics.get("age"),
        sex=demographics.get("sex"),
        is_pregnant=bool(demographics.get("is_pregnant")),
        is_postpartum=bool(demographics.get("is_postpartum")),
        pregnancy_week=demographics.get("pregnancy_week"),
        chronic_conditions=demographics.get("chronic") or [],
    )


def _patient_context(vignette: Vignette) -> dict:
    """The shape `consult.run` expects, built from vignette demographics."""
    demographics = vignette.demographics
    return {
        "age": demographics.get("age"),
        "sex": demographics.get("sex"),
        "is_pregnant": bool(demographics.get("is_pregnant")),
        "is_postpartum": bool(demographics.get("is_postpartum")),
        "pregnancy_week": demographics.get("pregnancy_week"),
        "chronic_conditions": demographics.get("chronic") or [],
    }


# --------------------------------------------------------------------------
# Arms A and A-full — deterministic
# --------------------------------------------------------------------------
def _deterministic(vignette: Vignette, *, text: str, arm: str) -> ArmResult:
    started = time.perf_counter()
    red_flags = rfe.assess_text(text, _context(vignette))
    recommendation = navigation.navigate(
        red_flags=red_flags, chief_complaint=vignette.opening
    )
    return ArmResult(
        vignette_id=vignette.id,
        arm=arm,
        urgency=str(red_flags.urgency),
        specialty=recommendation.specialty_code,
        rules=sorted(rule.rule_id for rule in red_flags.triggered_rules),
        questions_asked=0,
        latency_ms=int((time.perf_counter() - started) * 1000),
        source="red_flag_engine",
    )


def run_arm_a(vignette: Vignette) -> ArmResult:
    """Rules, on the opening message alone."""
    return _deterministic(vignette, text=vignette.opening, arm="A")


def run_arm_a_full(vignette: Vignette) -> ArmResult:
    """Rules, given everything the patient would have said if asked."""
    text = " ".join([vignette.opening, *vignette.followups.values()])
    return _deterministic(vignette, text=text, arm="A-full")


# --------------------------------------------------------------------------
# Arm B — a single LLM call
# --------------------------------------------------------------------------
_ARM_B_SYSTEM = """You are a clinical triage assistant for Sri Lanka.

Classify the urgency of the patient's message into exactly one of:
  emergency  - needs care right now (call an ambulance / go to A&E)
  urgent     - needs to be seen today or tomorrow
  routine    - should book a normal appointment
  self_care  - can be managed at home, or is not a clinical problem

Also name the single most appropriate specialty using one of these codes:
cardiology, neurology, respiratory_medicine, gastroenterology, general_surgery,
obstetrics_gynaecology, psychiatry, paediatrics, orthopaedics, general_medicine,
emergency_medicine, dermatology.

Reply with JSON only: {"urgency": "...", "specialty": "..."}"""


def run_arm_b(vignette: Vignette) -> ArmResult:
    """One LLM call, no rules, no conversation — the proposed simpler design."""
    started = time.perf_counter()
    demographics = vignette.demographics
    facts = [f"Age: {demographics.get('age')}", f"Sex: {demographics.get('sex')}"]
    if demographics.get("is_pregnant"):
        facts.append(f"Pregnant, week {demographics.get('pregnancy_week')}")
    if demographics.get("is_postpartum"):
        facts.append("Recently gave birth")
    if demographics.get("chronic"):
        facts.append("Known conditions: " + ", ".join(demographics["chronic"]))

    prompt = f"{chr(10).join(facts)}\n\nPatient says: {vignette.opening}"
    # Budgeted well above the ~20 tokens the answer needs. Groq's current
    # default model is a reasoning model that spends output tokens on a hidden
    # scratchpad before writing anything, so a tight budget yields empty
    # content and a 400 from JSON mode. Starving the arm we are arguing
    # against would make the comparison worthless.
    data, completion = llm.complete_json(
        prompt, system=_ARM_B_SYSTEM, temperature=0.0, max_tokens=500
    )

    if not completion.ok:
        return ArmResult(
            vignette_id=vignette.id, arm="B",
            latency_ms=int((time.perf_counter() - started) * 1000),
            source=completion.provider,
            error="no provider available",
        )
    if not data:
        return ArmResult(
            vignette_id=vignette.id, arm="B",
            latency_ms=int((time.perf_counter() - started) * 1000),
            source=completion.provider,
            error="unparseable response",
        )

    return ArmResult(
        vignette_id=vignette.id,
        arm="B",
        urgency=str(data.get("urgency") or "").strip().lower() or None,
        specialty=str(data.get("specialty") or "").strip().lower() or None,
        rules=[],
        questions_asked=0,
        latency_ms=int((time.perf_counter() - started) * 1000),
        source=completion.provider,
    )


# --------------------------------------------------------------------------
# Arm C — the live consultation loop
# --------------------------------------------------------------------------
def _answer_for(question: str, vignette: Vignette) -> str:
    """What this simulated patient says when asked `question`.

    Matched by concept rather than by keyword so the simulator rewards a
    system for asking about the *right thing*, however it chooses to word it.
    That property is what makes Phase 1's hypothesis-driven questioning
    measurable at all: ask about radiation and you learn about radiation, ask
    a generic opener and you get the generic answer.
    """
    if not question:
        return NEUTRAL_ANSWER

    # Resolve the question the same way the consultation engine does, via its
    # reverse index. Concept extraction alone is not enough: the curated
    # questions are deliberately natural, so "Does the pain spread anywhere —
    # into your arm, jaw, neck or back?" contains no lexicon surface form for
    # `radiating_pain` and a simulator relying on extraction would answer
    # every hypothesis question with the neutral filler — making the feature
    # look useless in exactly the benchmark built to evaluate it.
    concept = consult._concept_of_question(question)
    if concept and concept in vignette.followups:
        return vignette.followups[concept]

    # Sorted, and asserted before negated: set iteration order is not stable
    # across processes, and an unstable simulated patient would show up as
    # model non-determinism in the reproducibility table — measuring our own
    # harness instead of the system under test.
    asserted, negated = extract_concepts(question)
    for candidate in sorted(asserted) + sorted(negated - asserted):
        if candidate in vignette.followups:
            return vignette.followups[candidate]

    # Fall back to the generic history slot the question maps to, so scripted
    # answers keyed by "onset"/"severity" still work.
    slot = consult._slot_of_question(question)
    if slot and slot in vignette.followups:
        return vignette.followups[slot]

    # No scripted answer. A hypothesis question is a direct yes/no about a
    # specific concept, and this patient does not have it — so say so plainly
    # rather than returning filler, which would leave the rule live and let
    # the assistant ask around it until the cap.
    if concept:
        return "No, nothing like that."
    return NEUTRAL_ANSWER


def run_arm_c(vignette: Vignette) -> ArmResult:
    """Drive the real consultation engine through a full conversation."""
    started = time.perf_counter()
    patient_context = _patient_context(vignette)
    messages: list[dict] = [{"role": "user", "content": vignette.opening}]
    # Mirrors `agent/graph.py`, which records each turn's slot via
    # `memory.mark_asked(turn.slot)` and passes it back on the next call.
    # Without it the harness measures a system with amnesia: a model-phrased
    # question cannot always be mapped back to its concept from text alone,
    # so the same question gets asked repeatedly and the benchmark blames the
    # feature for a gap in the test rig.
    asked: list[str] = []
    questions = 0
    turn = None

    for _ in range(MAX_SIMULATED_TURNS):
        turn = consult.run(
            messages=messages,
            patient_context=patient_context,
            language=vignette.language,
            memory_asked=asked,
        )
        if turn.mode in ("assess", "escalate"):
            break

        if turn.slot and turn.slot not in asked:
            asked.append(turn.slot)
        questions += 1
        messages.append({"role": "assistant", "content": turn.question or turn.answer})
        messages.append({
            "role": "user",
            "content": _answer_for(turn.question or turn.answer, vignette),
        })
    else:
        return ArmResult(
            vignette_id=vignette.id, arm="C", questions_asked=questions,
            conversation=messages,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=f"did not conclude within {MAX_SIMULATED_TURNS} turns",
        )

    rules = [
        rule.get("rule_id")
        for rule in (turn.red_flags or {}).get("rules", [])
        if rule.get("rule_id")
    ]
    return ArmResult(
        vignette_id=vignette.id,
        arm="C",
        urgency=turn.urgency,
        specialty=turn.specialty,
        rules=sorted(rules),
        questions_asked=questions,
        conversation=messages,
        latency_ms=int((time.perf_counter() - started) * 1000),
        source=turn.source,
    )


def run_arm_c_generic(vignette: Vignette) -> ArmResult:
    """Arm C with hypothesis selection switched off — the previous behaviour.

    The point of comparison for Phase 1. Everything is identical except how
    the next question is chosen: this walks the six generic history slots in
    fixed order and stops at four, which is what the system did before, and
    what the review panel saw. Keeping it as a real arm rather than a
    throwaway patch means the claim can be re-checked at any time instead of
    resting on a number in a commit message.
    """
    with mock.patch.object(hypothesis, "next_target", lambda **_: (None, [])):
        result = run_arm_c(vignette)
    result.arm = "C-generic"
    return result


ARMS: dict[str, callable] = {
    "A": run_arm_a,
    "A-full": run_arm_a_full,
    "B": run_arm_b,
    "C-generic": run_arm_c_generic,
    "C": run_arm_c,
}

ARM_DESCRIPTIONS: dict[str, str] = {
    "A": "Deterministic rules, opening message only",
    "A-full": "Deterministic rules, opening + all follow-up answers",
    "B": "Single LLM call, opening message only",
    "C-generic": "Consultation loop, generic history slots (behaviour before Phase 1)",
    "C": "Consultation loop, hypothesis-driven questions",
}
