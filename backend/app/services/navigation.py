"""Care navigation engine.

Turns an assessed intake into an explainable recommendation:
possible care category, recommended specialty, urgency and next action
(spec §6). Every recommendation carries a reason, a confidence and a suggested
next action (internal rule 10).

Symptom intake, report extraction and image analysis all converge here, which
is what makes rules 4 and 5 hold — a CV finding or a lab result feeds care
navigation through exactly the same path as a conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.clinical.catalog import (
    CAPABILITY_TESTS,
    CONCEPT_CAPABILITIES,
    CONCEPT_SPECIALTY_WEIGHTS,
    SPECIALTY_BY_CODE,
    TEST_BY_CODE,
)
from app.clinical.lexicon import concept_label
from app.models.enums import UrgencyLevel
from app.services.red_flag_engine import RedFlagResult

CARE_CATEGORY_BY_URGENCY = {
    UrgencyLevel.EMERGENCY: "Emergency care",
    UrgencyLevel.URGENT: "Urgent medical assessment",
    UrgencyLevel.ROUTINE: "Scheduled specialist consultation",
    UrgencyLevel.SELF_CARE: "Self-care with monitoring",
}

NEXT_ACTION_BY_URGENCY = {
    UrgencyLevel.EMERGENCY: (
        "Go to the nearest emergency department with the required facilities, "
        "or call 1990 for an ambulance."
    ),
    UrgencyLevel.URGENT: "Book the earliest available appointment, ideally today or tomorrow.",
    UrgencyLevel.ROUTINE: "Book a consultation with the recommended specialty.",
    UrgencyLevel.SELF_CARE: (
        "Monitor your symptoms. Book a consultation if they persist beyond a "
        "few days or get worse."
    ),
}


@dataclass
class NavigationResult:
    care_category: str
    specialty_code: str
    secondary_specialty_codes: list[str]
    urgency: UrgencyLevel
    reason: str
    suggested_next_action: str
    confidence: float
    required_capabilities: list[str] = field(default_factory=list)
    recommended_tests: list[dict] = field(default_factory=list)
    patient_guidance: str | None = None

    @property
    def specialty_name(self) -> str:
        spec = SPECIALTY_BY_CODE.get(self.specialty_code)
        return spec.name if spec else self.specialty_code.replace("_", " ").title()


def navigate(
    *,
    red_flags: RedFlagResult,
    chief_complaint: str = "",
    extra_capabilities: list[str] | None = None,
    specialty_override: str | None = None,
    override_reason: str | None = None,
    source: str = "symptom",
) -> NavigationResult:
    """Produce an explainable care recommendation.

    `specialty_override` lets a report or image finding steer the specialty
    (e.g. a pneumonia pattern -> respiratory medicine) while still flowing
    through the shared urgency and capability logic.
    """
    concepts = red_flags.concepts
    scores = _score_specialties(concepts, red_flags)

    if specialty_override:
        specialty_code = specialty_override
        # Keep the scored alternatives as secondary options.
        secondary = [c for c, _ in scores if c != specialty_code][:2]
    elif scores:
        specialty_code = scores[0][0]
        secondary = [c for c, _ in scores[1:3]]
    else:
        specialty_code = "general_medicine"
        secondary = []

    capabilities = _collect_capabilities(concepts, red_flags, specialty_code, extra_capabilities)
    tests = _recommend_tests(capabilities, red_flags.urgency)
    confidence = _confidence(scores, red_flags, bool(specialty_override))

    reason = override_reason or _build_reason(
        specialty_code=specialty_code,
        concepts=concepts,
        red_flags=red_flags,
        chief_complaint=chief_complaint,
        source=source,
    )

    return NavigationResult(
        care_category=CARE_CATEGORY_BY_URGENCY[red_flags.urgency],
        specialty_code=specialty_code,
        secondary_specialty_codes=secondary,
        urgency=red_flags.urgency,
        reason=reason,
        suggested_next_action=_next_action(red_flags, specialty_code),
        confidence=confidence,
        required_capabilities=capabilities,
        recommended_tests=tests,
        patient_guidance=red_flags.escalation_message,
    )


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def _score_specialties(
    concepts: set[str], red_flags: RedFlagResult
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for concept in concepts:
        for specialty_code, weight in CONCEPT_SPECIALTY_WEIGHTS.get(concept, []):
            scores[specialty_code] = scores.get(specialty_code, 0.0) + weight

    # A fired red-flag rule names the clinically correct specialty; weight it
    # heavily so it outranks incidental symptom matches (internal rule 2).
    for hint in red_flags.specialty_hints:
        scores[hint] = scores.get(hint, 0.0) + 4.0

    # An emergency always routes through emergency medicine as an option.
    if red_flags.urgency is UrgencyLevel.EMERGENCY:
        scores["emergency_medicine"] = scores.get("emergency_medicine", 0.0) + 2.0

    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _collect_capabilities(
    concepts: set[str],
    red_flags: RedFlagResult,
    specialty_code: str,
    extra: list[str] | None,
) -> list[str]:
    caps: list[str] = list(red_flags.required_capabilities)

    for concept in sorted(concepts):
        caps.extend(CONCEPT_CAPABILITIES.get(concept, []))

    spec = SPECIALTY_BY_CODE.get(specialty_code)
    if spec:
        caps.append(spec.capability)

    if extra:
        caps.extend(extra)

    # Emergency capability takes priority over any preference (internal rule 3).
    if red_flags.requires_emergency_facility and "emergency" not in caps:
        caps.insert(0, "emergency")

    seen: set[str] = set()
    return [c for c in caps if not (c in seen or seen.add(c))]


def _recommend_tests(capabilities: list[str], urgency: UrgencyLevel) -> list[dict]:
    """Map required capabilities to concrete, orderable tests."""
    limit = 5 if urgency in (UrgencyLevel.EMERGENCY, UrgencyLevel.URGENT) else 4
    out: list[dict] = []
    seen: set[str] = set()

    for capability in capabilities:
        for test_code in CAPABILITY_TESTS.get(capability, [])[:2]:
            if test_code in seen:
                continue
            test = TEST_BY_CODE[test_code]
            seen.add(test_code)
            out.append(
                {
                    "code": test.code,
                    "name": test.name,
                    "category": test.category,
                    "required_capability": test.required_capability,
                    "typical_price_lkr": test.price_lkr,
                    "preparation": test.preparation,
                }
            )
            if len(out) >= limit:
                return out
    return out


def _confidence(
    scores: list[tuple[str, float]], red_flags: RedFlagResult, overridden: bool
) -> float:
    """Confidence rises with a decisive score margin and with fired rules."""
    if not scores:
        return 0.35

    top = scores[0][1]
    runner_up = scores[1][1] if len(scores) > 1 else 0.0
    margin = (top - runner_up) / top if top else 0.0

    base = 0.5 + 0.3 * margin
    if red_flags.has_red_flags:
        base += 0.15  # a deterministic rule fired, so routing is well-grounded
    if overridden:
        base += 0.05  # a model finding pinned the specialty directly
    if len(red_flags.concepts) <= 1:
        base -= 0.12  # very little to go on

    return round(max(0.3, min(0.95, base)), 2)


def _build_reason(
    *,
    specialty_code: str,
    concepts: set[str],
    red_flags: RedFlagResult,
    chief_complaint: str,
    source: str,
) -> str:
    spec = SPECIALTY_BY_CODE.get(specialty_code)
    specialty_name = spec.name if spec else specialty_code.replace("_", " ").title()

    if red_flags.has_red_flags:
        top = red_flags.triggered_rules[0]
        return (
            f"Your reported symptoms match a recognised warning pattern "
            f"({top.label.lower()}). {top.rationale} "
            f"{specialty_name} is the appropriate specialty for this assessment."
        )

    relevant = [
        concept_label(c).lower()
        for c in sorted(concepts)
        if any(s == specialty_code for s, _ in CONCEPT_SPECIALTY_WEIGHTS.get(c, []))
    ][:3]

    if relevant:
        symptom_text = ", ".join(relevant)
        origin = {
            "symptom": "Your reported symptoms",
            "report": "The findings in your uploaded report",
            "image": "The screening result from your uploaded image",
        }.get(source, "Your reported information")
        return (
            f"{origin} primarily involve {symptom_text}, which requires "
            f"assessment by {specialty_name}."
        )

    if chief_complaint:
        return (
            f"Based on your description of \"{chief_complaint.strip()[:120]}\", "
            f"{specialty_name} is the most appropriate starting point for "
            f"assessment."
        )

    return (
        f"{specialty_name} is the most appropriate starting point for assessing "
        f"the information you provided."
    )


def _next_action(red_flags: RedFlagResult, specialty_code: str) -> str:
    spec = SPECIALTY_BY_CODE.get(specialty_code)
    specialty_name = spec.name if spec else specialty_code.replace("_", " ").title()

    if red_flags.urgency is UrgencyLevel.EMERGENCY:
        return NEXT_ACTION_BY_URGENCY[UrgencyLevel.EMERGENCY]
    if red_flags.urgency is UrgencyLevel.URGENT:
        return (
            f"Book the earliest available {specialty_name} appointment — ideally "
            f"today or tomorrow."
        )
    if red_flags.urgency is UrgencyLevel.ROUTINE:
        return f"Book a consultation with a {specialty_name} specialist."
    return NEXT_ACTION_BY_URGENCY[UrgencyLevel.SELF_CARE]
