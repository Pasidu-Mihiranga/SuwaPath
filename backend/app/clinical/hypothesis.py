"""Choosing the next question by reasoning backward from the rule set.

`consult.py` used to ask the first unfilled slot from a fixed list of six
generic history topics — onset, character, severity, associated, pattern,
history — in the same order for every complaint, and stop at four. Its own
docstring claimed it picked "the question whose answer would most change which
explanation fits". It did not; nothing in the codebase computed that.

Measurement showed why this mattered. Running the evaluation harness over 105
labelled vignettes, the deterministic engine given only the opening message and
the same engine given *every* follow-up answer scored identically — 100%
emergency recall, same accuracy, same errors, on every single case. Four
questions were being asked and none of them changed an outcome.

This module computes the thing the docstring promised. Every red-flag rule is a
conjunction of concept groups, so for any point in a conversation each rule sits
some number of concepts away from firing. A rule one concept away from an
EMERGENCY verdict is the most valuable question in the room; a rule that can no
longer fire — because the patient denied its only remaining option, or because
its context check excludes them — is worth nothing and must be dropped rather
than merely ranked low.

Deliberately *not* an information-theoretic model. Expected information gain
would need per-concept prevalence priors that this project has no data to
estimate, and inventing them would produce confident arithmetic resting on
made-up numbers. Distance-to-firing weighted by urgency uses only what the rule
set actually states.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.clinical.catalog import CONCEPT_SPECIALTY_WEIGHTS
from app.clinical.red_flag_rules import RULES, Context, RedFlagRule


@dataclass(frozen=True)
class RuleGap:
    """How far one rule is from firing, and what would close it."""

    rule: RedFlagRule
    needed: int                      # concepts still required
    live_groups: list[set[str]]      # unmet groups, denied options removed
    # True when the patient's own words put this rule in play; False when only
    # their context did (pregnant, infant, older adult). Drives the two-tier
    # preference in `next_target`.
    from_symptoms: bool = True

    @property
    def is_one_away(self) -> bool:
        return self.needed == 1


# Concepts that only exist as a patient's own account of an inner experience.
# Nobody can tell you a one-year-old has a headache or feels dizzy, so asking
# is worse than useless: the carer answers "no" out of politeness and the
# concept is recorded as denied. Observable signs — breathing, fever,
# floppiness, feeding — carry the paediatric assessment instead.
_SELF_REPORT_ONLY = {
    "headache", "severe_headache", "confusion", "dizziness", "blurred_vision",
    "chest_pain", "radiating_pain", "palpitations", "nausea", "sore_throat",
    "eye_pain", "hearing_loss", "joint_pain", "abdominal_pain",
    "severe_abdominal_pain", "low_mood", "suicidal_ideation", "night_sweats",
    "excessive_thirst", "cold_intolerance",
}
_SELF_REPORT_MIN_AGE = 5


def is_applicable(concept: str, context: Context) -> bool:
    """Can this patient meaningfully answer a question about this concept?"""
    age = context.get("age")
    if age is not None and age < _SELF_REPORT_MIN_AGE:
        return concept not in _SELF_REPORT_ONLY
    return True


def _top_specialty(concept: str) -> str | None:
    """The specialty a concept points at most strongly, per the catalogue."""
    weights = CONCEPT_SPECIALTY_WEIGHTS.get(concept)
    if not weights:
        return None
    return max(weights, key=lambda pair: pair[1])[0]


def _shares_specialty(rule: RedFlagRule, asserted: set[str]) -> bool:
    """Is this rule about the same body system the patient is describing?

    Concept overlap alone is too literal. Someone reporting `abdominal_pain`
    never matches RF-GI-001, whose concepts are `vomiting_blood` and
    `blood_in_stool` — so the assistant never asked about blood, and a GI
    bleed presenting as stomach ache was missed in evaluation. Both concepts
    and the rule point at gastroenterology, which is the connection a
    clinician makes immediately.

    The catalogue's existing concept-to-specialty weights supply this; only
    the single strongest specialty per concept counts, because using every
    weighted specialty makes almost everything relevant to almost everything.
    """
    if not rule.specialty_code:
        return False
    return any(_top_specialty(concept) == rule.specialty_code for concept in asserted)


def _mentions(rule: RedFlagRule, asserted: set[str]) -> bool:
    """Has the patient named a concept this rule is actually built from?"""
    return any(group & asserted for group in (*rule.all_of, *rule.any_of))


# Rule families that exist *for* a particular kind of patient, as opposed to
# rules that merely take a risk factor into account.
_POPULATION_CATEGORIES = {"maternal", "paediatric", "geriatric"}


def _defines_this_patient(rule: RedFlagRule, context: Context) -> bool:
    """Is this patient the kind of person the rule was written for?

    Being pregnant, an infant or an older adult is a strong reason to run that
    rule family's questions whatever the complaint. Having a cardiac risk
    factor is not the same thing: `_cardiac_risk` is satisfied by age 45 or a
    diabetes diagnosis, so treating it the same way had every middle-aged
    patient asked about chest pain first regardless of why they came — "my
    knee hurts when I climb stairs" was answered with "do you have any pain or
    tightness in your chest?". Risk modifiers still admit the rule; they just
    do not push it ahead of what the patient actually said.
    """
    if rule.context_check is None or rule.category not in _POPULATION_CATEGORIES:
        return False
    return rule.context_check(context)


def rule_gap(
    rule: RedFlagRule, asserted: set[str], negated: set[str], context: Context
) -> RuleGap | None:
    """Distance from firing, or None when the rule can no longer fire at all.

    The None cases matter as much as the arithmetic. A maternal rule evaluated
    for a male patient, or a rule whose only remaining option the patient has
    explicitly denied, is dead — not low priority. Ranking it low would still
    let it surface once everything above it is exhausted, and the assistant
    would ask a question whose answer cannot change anything.
    """
    if rule.context_check and not rule.context_check(context):
        return None

    needed = 0
    live_groups: list[set[str]] = []

    for group in rule.all_of:
        if group & asserted:
            continue                       # already satisfied
        live = group - negated
        if not live:
            return None                    # every alternative denied
        needed += 1
        live_groups.append(live)

    if rule.any_of:
        satisfied = sum(1 for group in rule.any_of if group & asserted)
        still_needed = max(0, rule.any_of_min - satisfied)
        if still_needed:
            candidates = [
                group - negated
                for group in rule.any_of
                if not (group & asserted) and (group - negated)
            ]
            if len(candidates) < still_needed:
                return None                # not enough live options remain
            needed += still_needed
            live_groups.extend(candidates)

    # Three strengths of relevance, collapsed into two tiers.
    #
    # Naming a concept the rule is built from, or being the patient the rule
    # exists for (an infant, a pregnancy), are both strong: a feverish
    # one-year-old must be asked about feeding before being asked the adult
    # fever questions, and demoting context below concepts cost exactly that
    # case in evaluation.
    #
    # Sharing a specialty is weaker — it is how "stomach pain" reaches the
    # GI-bleed rule, but it also drags in every other rule for that body
    # system. Left in the same tier it crowded out the one question that
    # mattered: a thunderclap headache pulled in the stroke and seizure rules
    # and ran out of questions before asking about vomiting.
    first_tier = _mentions(rule, asserted) or _defines_this_patient(rule, context)

    return RuleGap(
        rule=rule,
        needed=needed,
        live_groups=live_groups,
        from_symptoms=first_tier,
    )


def is_engaged(rule: RedFlagRule, asserted: set[str], context: Context) -> bool:
    """Has the patient given any reason to be thinking about this rule?

    Distance to firing alone is not enough, and the first version of this was
    wrong because of it. Several EMERGENCY rules are a single concept —
    `loss_of_consciousness`, `seizure`, `severe_bleeding` — so they sit
    permanently one concept from firing for *everyone*, whatever the
    complaint. They swamped the scoring, and "I have a bad headache" was
    answered with "have you fainted or blacked out?", a screening question
    with no connection to the story being told.

    A rule earns consideration three ways: the patient named a concept it is
    built from; they *are* the kind of patient it exists for (pregnant, an
    infant, an older adult); or it concerns the same body system they are
    describing. The second is what lets a pregnant woman reporting a headache
    be asked the pre-eclampsia questions before anything in her words points
    there. The third is what lets "stomach pain" reach the GI-bleed rule.

    A bare risk factor is deliberately not enough. `_cardiac_risk` holds for
    anyone over 45, so admitting rules on that alone had a knee complaint
    answered with "do you have any chest pain?" — a question with no
    connection to what was asked. Such a rule still fires the moment the
    patient mentions something relevant; it just does not get to choose the
    questions.
    """
    return (
        _mentions(rule, asserted)
        or _defines_this_patient(rule, context)
        or _shares_specialty(rule, asserted)
    )


def open_gaps(
    asserted: set[str], negated: set[str], context: Context
) -> list[RuleGap]:
    """Every rule still in play for *this* patient, with its distance."""
    gaps = []
    for rule in RULES:
        if not is_engaged(rule, asserted, context):
            continue
        gap = rule_gap(rule, asserted, negated, context)
        # needed == 0 means the rule has already fired; the caller handles
        # that through the engine, not through question selection.
        if gap is not None and gap.needed > 0:
            gaps.append(gap)
    return gaps


def concept_scores(gaps: list[RuleGap]) -> dict[str, float]:
    """Rank candidate concepts by how much finding one out would settle.

    `urgency.rank / needed²` makes a rule one concept from an emergency worth
    far more than one three concepts from a routine referral, and the square
    means distance drops off sharply — asking toward a rule that still needs
    three separate answers is close to guessing.

    Credit is split across the alternatives within a group so a group listing
    four ways to satisfy it does not outweigh a group with one specific
    answer. Scores accumulate across rules, so a concept that would close
    several rules at once — `shortness_of_breath` closes both RF-CARD-001 and
    RF-RESP-003 — naturally rises to the top. That accumulation is the whole
    point: it is what "most discriminating" means here.
    """
    scores: dict[str, float] = {}
    for gap in gaps:
        weight = gap.rule.urgency.rank / (gap.needed ** 2)
        for group in gap.live_groups:
            share = weight / len(group)
            for concept in group:
                scores[concept] = scores.get(concept, 0.0) + share
    return scores


def next_target(
    *,
    asserted: set[str],
    negated: set[str],
    context: Context,
    already_asked: set[str],
    min_score: float = 0.0,
) -> tuple[str | None, list[RuleGap]]:
    """The concept most worth asking about next, and the live hypotheses.

    Returns `(None, gaps)` when nothing is worth asking — either no rule can
    still fire, or every useful concept has been asked about already. The
    caller then falls back to generic history taking rather than inventing a
    question, which keeps the old behaviour as the floor.
    """
    gaps = open_gaps(asserted, negated, context)
    if not gaps:
        return None, []

    # Two tiers, not a weighting. Follow up what the patient actually told us
    # before running context screens at them: a pregnant woman who reports a
    # headache should be asked about neck stiffness and vision before she is
    # asked whether her waters have broken. The screens are not dropped — they
    # take over once the symptom-driven questions are exhausted, which is the
    # right moment for them.
    #
    # Preferred over a relevance multiplier because any multiplier that
    # produced this ordering would be a constant chosen to produce it, dressed
    # up as arithmetic.
    for tier in (True, False):
        tier_gaps = [gap for gap in gaps if gap.from_symptoms is tier]
        if not tier_gaps:
            continue
        scores = concept_scores(tier_gaps)
        for concept in asserted | negated | already_asked:
            scores.pop(concept, None)
        scores = {
            concept: score
            for concept, score in scores.items()
            if is_applicable(concept, context)
        }
        if not scores:
            continue
        # Sorted by name before max() so ties resolve identically every run.
        # An assistant that asks a different question on a replay of the same
        # conversation cannot be evaluated or audited.
        target, best = max(sorted(scores.items()), key=lambda item: item[1])
        if best > min_score:
            return target, gaps

    return None, gaps


# --------------------------------------------------------------------------
# Wording
# --------------------------------------------------------------------------
# Curated question text per concept. Hand-written rather than harvested: the
# old engine's TARGETED_PROBES table is keyed by ad-hoc sub-labels
# ("radiation", "neck") that are not lexicon concept ids, so a mechanical
# merge would have produced entries that silently never match.
#
# Each question is phrased so that a plain "yes" or "no" is a usable answer,
# because that is what patients actually type.
CONCEPT_PHRASING: dict[str, str] = {
    # Cardiac
    "radiating_pain": "Does the pain spread anywhere — into your arm, jaw, neck or back?",
    "sweating": "Have you been sweating or feeling clammy with it?",
    "shortness_of_breath": "Are you also finding it hard to breathe?",
    "palpitations": "Does your heart feel like it is racing or pounding?",
    "chest_pain": "Do you have any pain or tightness in your chest?",
    "loss_of_consciousness": "Have you fainted or blacked out at any point?",
    # Neurological
    "facial_droop": "Has one side of your face started drooping?",
    "arm_weakness": "Is there weakness or numbness down one side of your body?",
    "speech_difficulty": "Has your speech become slurred or hard to get out?",
    "neck_stiffness": "Is your neck stiff, or painful when you bend it forward?",
    "confusion": "Have you felt confused or muddled, or has anyone said you seem it?",
    "seizure": "Have you had any fits or convulsions?",
    "blurred_vision": "Has your vision changed at all — blurred, double, or any loss?",
    "severe_headache": "How bad is the headache — is it the worst you have ever had?",
    "headache": "Do you have a headache with it?",
    "rash": "Have you noticed any rash or spots on the skin?",
    "dizziness": "Have you felt dizzy or light-headed?",
    # Respiratory
    "coughing_blood": "Have you coughed up any blood?",
    "cough": "Do you have a cough with it?",
    # Constitutional / infection
    "high_fever": "Have you taken your temperature — is the fever high?",
    "fever": "Do you have a fever?",
    # GI / bleeding
    "vomiting": "Have you been vomiting?",
    "vomiting_blood": "Has there been any blood in the vomit, or anything like coffee grounds?",
    "blood_in_stool": "Have you noticed blood in your stools, or very black stools?",
    "severe_abdominal_pain": "How severe is the stomach pain — is it stopping you standing straight?",
    "severe_bleeding": "Is the bleeding heavy, or is it not stopping?",
    "injury": "Did you have a fall or injure yourself?",
    # Maternal
    "vaginal_bleeding": "Have you had any bleeding?",
    "reduced_fetal_movement": "Have the baby's movements slowed down or changed?",
    "leaking_fluid": "Have you had any fluid leaking, or do you think your waters may have broken?",
    "swelling": "Have you noticed swelling in your hands, face or feet?",
    # Paediatric
    "child_not_feeding": "Is the baby feeding normally?",
    "child_lethargy": "Is the baby unusually sleepy or floppy?",
    # Mental health
    "suicidal_ideation": "Have you had any thoughts of harming yourself?",
}


def question_for(concept: str) -> str | None:
    """Curated wording for a concept, or None to let the model phrase it."""
    return CONCEPT_PHRASING.get(concept)
