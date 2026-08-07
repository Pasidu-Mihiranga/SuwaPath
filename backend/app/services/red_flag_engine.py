"""Deterministic red-flag / urgency engine.

This layer is intentionally *not* AI. The LLM extracts structure from the
conversation; this engine decides the care level from that structure using
fixed clinical rules (internal rule 1). Its output outranks every downstream
ranking score (internal rule 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.clinical.lexicon import concept_label, extract_concepts
from app.clinical.red_flag_rules import (
    RULE_ENGINE_VERSION,
    RULES,
    Context,
    RedFlagRule,
)
from app.models.enums import UrgencyLevel

ESCALATION_MESSAGES: dict[UrgencyLevel, str] = {
    UrgencyLevel.EMERGENCY: (
        "Seek urgent medical attention now. Go to the nearest emergency "
        "department or call 1990 (Suwa Seriya ambulance). Do not drive "
        "yourself if you feel unwell."
    ),
    UrgencyLevel.URGENT: (
        "Arrange to be seen by a doctor today or tomorrow. If your symptoms "
        "get worse before then, go to an emergency department."
    ),
    UrgencyLevel.ROUTINE: (
        "Book a consultation with the recommended specialty at your "
        "convenience. Seek care sooner if your symptoms change or worsen."
    ),
    UrgencyLevel.SELF_CARE: (
        "Your reported symptoms do not currently suggest a need for urgent "
        "care. Monitor how you feel and book a consultation if things do not "
        "improve or get worse."
    ),
}

# Concepts that on their own justify lifting a case above pure self-care.
NON_TRIVIAL_CONCEPTS = {
    "chest_pain", "shortness_of_breath", "severe_headache", "skin_lesion",
    "coughing_blood", "vaginal_bleeding", "genital_symptoms", "joint_pain",
    "injury", "abdominal_pain", "blurred_vision", "eye_pain", "low_mood",
    "weight_loss", "palpitations", "hearing_loss", "diarrhoea", "rash",
    "fever", "fatigue", "dizziness", "cough", "swelling", "confusion",
    "headache", "sore_throat", "vomiting", "nausea", "night_sweats",
    "excessive_thirst", "frequent_urination", "hair_loss", "weight_gain",
    "cold_intolerance", "wheezing", "seizure", "contractions",
}


@dataclass
class TriggeredRule:
    rule_id: str
    label: str
    category: str
    urgency: UrgencyLevel
    matched_concepts: list[str]
    rationale: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "label": self.label,
            "category": self.category,
            "urgency": str(self.urgency),
            "matched_terms": [concept_label(c) for c in self.matched_concepts],
            "matched_concepts": self.matched_concepts,
            "rationale": self.rationale,
        }


@dataclass
class RedFlagResult:
    urgency: UrgencyLevel
    triggered_rules: list[TriggeredRule] = field(default_factory=list)
    escalation_message: str = ""
    requires_emergency_facility: bool = False
    required_capabilities: list[str] = field(default_factory=list)
    specialty_hints: list[str] = field(default_factory=list)
    concepts: set[str] = field(default_factory=set)
    negated_concepts: set[str] = field(default_factory=set)
    engine_version: str = RULE_ENGINE_VERSION

    @property
    def has_red_flags(self) -> bool:
        return bool(self.triggered_rules)

    def rules_as_dicts(self) -> list[dict]:
        return [r.to_dict() for r in self.triggered_rules]


def build_context(
    *,
    age: int | None = None,
    sex: str | None = None,
    is_pregnant: bool = False,
    is_postpartum: bool = False,
    pregnancy_week: int | None = None,
    chronic_conditions: list[str] | None = None,
) -> Context:
    """Assemble the patient context the rules evaluate against."""
    return {
        "age": age,
        "sex": sex,
        "is_pregnant": is_pregnant,
        "is_postpartum": is_postpartum,
        "pregnancy_week": pregnancy_week,
        "chronic": set(chronic_conditions or []),
    }


def assess_concepts(concepts: set[str], context: Context) -> RedFlagResult:
    """Run every rule against a concept set and return the strongest outcome."""
    triggered: list[TriggeredRule] = []
    capabilities: list[str] = []
    specialty_hints: list[str] = []

    for rule in RULES:
        matched = rule.evaluate(concepts, context)
        if matched is None:
            continue
        triggered.append(
            TriggeredRule(
                rule_id=rule.id,
                label=rule.label,
                category=rule.category,
                urgency=rule.urgency,
                matched_concepts=matched,
                rationale=rule.rationale,
            )
        )
        capabilities.extend(rule.required_capabilities)
        if rule.specialty_code:
            specialty_hints.append(rule.specialty_code)

    urgency = _resolve_urgency(triggered, concepts)

    # Order rules strongest-first so the UI leads with the most serious finding.
    triggered.sort(key=lambda r: r.urgency.rank, reverse=True)

    requires_emergency = urgency is UrgencyLevel.EMERGENCY or "emergency" in capabilities

    return RedFlagResult(
        urgency=urgency,
        triggered_rules=triggered,
        escalation_message=ESCALATION_MESSAGES[urgency],
        requires_emergency_facility=requires_emergency,
        required_capabilities=_dedupe(capabilities),
        specialty_hints=_dedupe(specialty_hints),
        concepts=concepts,
    )


def assess_text(text: str, context: Context) -> RedFlagResult:
    """Convenience wrapper: extract concepts from free text, then assess."""
    asserted, negated = extract_concepts(text)
    result = assess_concepts(asserted, context)
    result.negated_concepts = negated
    return result


def _resolve_urgency(
    triggered: list[TriggeredRule], concepts: set[str]
) -> UrgencyLevel:
    """Highest triggered urgency, or a baseline when no rule fires."""
    if triggered:
        return max((r.urgency for r in triggered), key=lambda u: u.rank)
    if concepts & NON_TRIVIAL_CONCEPTS:
        return UrgencyLevel.ROUTINE
    return UrgencyLevel.SELF_CARE


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving de-duplication."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
