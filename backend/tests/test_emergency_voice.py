"""What the hands-free listener will and will not summon an ambulance for.

Why this suite exists
---------------------
Every other route into the red-flag engine has a human in the loop. A patient
types a sentence, reads the reply, and decides what to do about it. The voice
listener removes all three of those: it screens a sentence nobody chose to
submit, and on a match it alerts emergency departments and wakes up a
guardian's phone before anyone has looked at the screen.

That changes what a wrong answer costs, in both directions:

  * **A false positive** now has a real-world recipient. An emergency
    department that receives three fictional inbound alerts learns to ignore
    the fourth, which is the one that mattered. So the everyday-speech cases
    below are not padding — they are the reason this can be left switched on.

  * **A false negative** is the whole failure. The listener exists for the
    minute when the patient cannot type, so "it would have matched if they had
    phrased it the way people type" is not a defence.

The second is why the spoken-phrasing cases are pinned so specifically.
Dictation does not produce typed text: it expands contractions ("cannot
breathe", never "can't breathe"), it puts the subject first ("the bleeding
won't stop", not "won't stop bleeding"), and it phrases reduced fetal movement
as a denial ("I haven't felt the baby move"). Each of those cost a concept
that emergency rules depend on, and each is pinned here so a future lexicon
tidy-up cannot quietly take it back.

The last check is not about the engine at all: it asserts that every rule that
can fire an emergency has a hand-written first-aid script on the frontend. The
avatar reads the script aloud with no interaction on this path, so a rule
without one produces an avatar that says "this is an emergency" and then has
nothing to say about what to do — at the exact moment someone is listening to
it instead of reading.

Needs no server and no database.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.clinical.red_flag_rules import RULES  # noqa: E402
from app.eval import vignettes  # noqa: E402
from app.models.enums import UrgencyLevel  # noqa: E402
from app.services.emergency import screen  # noqa: E402
from app.services.red_flag_engine import build_context  # noqa: E402

FIRST_AID_TS = (
    ROOT.parent / "frontend" / "src" / "components" / "Avatar" / "firstAid.ts"
)

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> list[str]:
    if condition:
        PASSED.append(label)
        return []
    FAILED.append(label)
    return [f"{label}{f' — {detail}' if detail else ''}"]


def _screen(text: str, **context) -> object:
    return screen(text, build_context(**context))


# --------------------------------------------------------------------------
# Spoken emergencies must fire
# --------------------------------------------------------------------------
# Sentence, patient context, and the rule that must fire. One per first-aid
# family, plus the Sinhala and Tamil paths, because the concept lexicon is the
# only reason those work and it is the thing most likely to be edited.
MUST_TRIGGER: list[tuple[str, dict, str]] = [
    ("I have chest pain and I am sweating a lot", {}, "RF-CARD-001"),
    ("My chest is tight and I cannot breathe", {}, "RF-CARD-001"),
    ("My father collapsed and passed out", {}, "RF-CARD-003"),
    ("His face is drooping and he cannot speak properly", {}, "RF-NEURO-001"),
    ("She is having a fit right now", {}, "RF-NEURO-004"),
    ("The bleeding will not stop", {}, "RF-BLEED-001"),
    ("I am vomiting blood", {}, "RF-GI-001"),
    (
        "I have the worst headache of my life and I keep vomiting",
        {},
        "RF-NEURO-003",
    ),
    # Fever plus a stiff neck is not enough on its own — the meningitis rule
    # wants a third finding — so the sentence carries one, as a real report of
    # it would.
    (
        "She has a high fever, a stiff neck and a terrible headache",
        {},
        "RF-NEURO-002",
    ),
    ("I am bleeding down there", {"is_pregnant": True}, "RF-MAT-001"),
    ("I have not felt the baby move today", {"is_pregnant": True}, "RF-MAT-003"),
    ("I want to die", {}, "RF-MH-001"),
    # Each of the five below was a silent false negative found by screening
    # sentences written the way they get *said* rather than typed. They are
    # pinned individually because they failed for five different reasons.
    #
    # Direction of travel instead of the word "radiating", and "hurting"
    # rather than "hurts" — the chest proximity rule did not list the
    # progressive form.
    (
        "My chest is hurting and the pain is going down my left arm",
        {},
        "RF-CARD-001",
    ),
    # Subject-first stroke reporting: the limb before the deficit, and
    # "speech is slurred" rather than "slurred speech".
    (
        "My mother's speech is slurred and her arm has gone weak",
        {},
        "RF-NEURO-001",
    ),
    # Two findings joined by "and", where the "not " inside "cannot" reached
    # across the conjunction and negated the swelling in the second clause.
    ("She cannot breathe and her face is swelling up", {}, "RF-RESP-001"),
    # Adverb after the verb: "bleeding heavily", not "heavy bleeding". The
    # postpartum rule had no other route in.
    (
        "I am bleeding heavily since the delivery",
        {"is_postpartum": True},
        "RF-MAT-005",
    ),
    # Dictation expands the contraction, which cost the concept outright.
    ("I cannot move my arm and my face feels numb", {}, "RF-NEURO-001"),
    (
        "My baby is not feeding and is very sleepy",
        {"age": 1},
        "RF-PAED-001",
    ),
    # Sinhala native script and romanised, and Tamil romanised. A Sinhala
    # speaker must reach the same rule as an English one.
    ("මට පපුවේ කැක්කුම සහ හුස්ම ගැනීමේ අපහසුතාව", {}, "RF-CARD-001"),
    ("papuwe kakkuma and husma ganna amaruyi", {}, "RF-CARD-001"),
    ("maarbu vali and moochu thinaral", {}, "RF-CARD-001"),
]


def spoken_emergencies_are_heard() -> list[str]:
    failures: list[str] = []
    for text, context, expected_rule in MUST_TRIGGER:
        result = _screen(text, **context)
        fired = [r.rule_id for r in result.triggered_rules]
        failures += check(
            f"triggers {expected_rule}: {text[:44]!r}",
            result.urgency == UrgencyLevel.EMERGENCY and expected_rule in fired,
            f"urgency={result.urgency} rules={fired}",
        )
    return failures


# --------------------------------------------------------------------------
# Everyday speech must not
# --------------------------------------------------------------------------
# The listener is armed all day and hears ordinary conversation, app narration
# and other people's small talk. Anything here that starts dispatching
# ambulances makes the feature unusable, so these are as load-bearing as the
# positives above.
MUST_NOT_TRIGGER: list[tuple[str, dict]] = [
    ("I have a mild headache since yesterday", {}),
    ("I have no chest pain, just a sore throat", {}),
    ("There is no more bleeding now", {}),
    ("I need to book an appointment with a dermatologist", {}),
    ("I cut my finger while cooking, it is a small cut", {}),
    ("My knee has been aching for a couple of weeks", {}),
    ("Can you explain my cholesterol result", {}),
    ("The baby is feeding well and sleeping through the night", {"age": 1}),
    ("I felt a bit dizzy when I stood up too fast", {}),
    ("What time is my appointment on Thursday", {}),
]


def everyday_speech_is_left_alone() -> list[str]:
    failures: list[str] = []
    for text, context in MUST_NOT_TRIGGER:
        result = _screen(text, **context)
        failures += check(
            f"stays quiet: {text[:44]!r}",
            result.urgency != UrgencyLevel.EMERGENCY,
            f"urgency={result.urgency} rules={[r.rule_id for r in result.triggered_rules]}",
        )
    return failures


# --------------------------------------------------------------------------
# The negation fix must not have broken denial
# --------------------------------------------------------------------------
def denials_still_deny() -> list[str]:
    """A cessation verb inverts a denial; nothing else should.

    `NEGATION_BLOCKERS` exists so "the bleeding won't stop" asserts bleeding
    rather than denying it. The risk it introduces is the opposite one — a
    blocker that fires too eagerly turns every "no" into a "yes" — so the
    ordinary denials are pinned alongside it.
    """
    from app.clinical.lexicon import extract_concepts

    failures: list[str] = []

    asserted, negated = extract_concepts("Her arm is bleeding a lot and it wont stop")
    failures += check(
        "cessation phrasing asserts the symptom",
        "severe_bleeding" in asserted,
        f"asserted={sorted(asserted)} negated={sorted(negated)}",
    )

    asserted, negated = extract_concepts("There is no more bleeding now")
    failures += check(
        "'no more' is still a denial",
        "severe_bleeding" not in asserted,
        f"asserted={sorted(asserted)}",
    )

    asserted, negated = extract_concepts("No fever, no vomiting, just a mild headache")
    failures += check(
        "a denial does not swallow the next clause",
        "headache" in asserted and {"fever", "vomiting"} <= negated,
        f"asserted={sorted(asserted)} negated={sorted(negated)}",
    )

    asserted, _ = extract_concepts("I have no chest pain at all")
    failures += check(
        "a plain denial still denies",
        "chest_pain" not in asserted,
        f"asserted={sorted(asserted)}",
    )

    # " and " as a clause reset. People say two findings in one breath, and
    # the "not " inside "cannot" used to negate the finding in the clause
    # after the conjunction.
    asserted, negated = extract_concepts(
        "She cannot breathe and her face is swelling up"
    )
    failures += check(
        "a conjunction ends the reach of a denial",
        {"shortness_of_breath", "swelling"} <= asserted,
        f"asserted={sorted(asserted)} negated={sorted(negated)}",
    )

    # The opposite direction of that same change: widening the resets must not
    # turn a genuine repeated denial into two assertions.
    asserted, negated = extract_concepts("I have no fever and no vomiting")
    failures += check(
        "a denial repeated after 'and' still denies both",
        not ({"fever", "vomiting"} & asserted),
        f"asserted={sorted(asserted)} negated={sorted(negated)}",
    )
    return failures


# --------------------------------------------------------------------------
# No side effects for the quiet case
# --------------------------------------------------------------------------
def screening_is_pure() -> list[str]:
    """Screening must not need a database, because most calls decide nothing.

    The listener screens every sentence it hears all day. If that path touched
    the database it would either be too slow to run that often or would leave a
    record of every sentence spoken near the phone. `screen()` takes no session
    for exactly that reason, and this pins the signature.
    """
    import inspect

    parameters = set(inspect.signature(screen).parameters)
    return check(
        "screen() takes no database session",
        parameters == {"transcript", "context"},
        f"parameters={sorted(parameters)}",
    )


# --------------------------------------------------------------------------
# Every emergency rule has something to say about what to do
# --------------------------------------------------------------------------
def every_emergency_rule_has_first_aid() -> list[str]:
    if not FIRST_AID_TS.exists():
        return check(
            "first-aid script file is where this expects it",
            False,
            f"missing {FIRST_AID_TS}",
        )

    source = FIRST_AID_TS.read_text(encoding="utf-8")
    # Only the ids inside `rules: [...]` arrays, so a rule id mentioned in a
    # comment does not count as covered.
    covered: set[str] = set()
    for block in re.findall(r"rules:\s*\[([^\]]*)\]", source):
        covered.update(re.findall(r"RF-[A-Z]+-\d+", block))

    emergency_rules = {
        rule.id for rule in RULES if rule.urgency == UrgencyLevel.EMERGENCY
    }
    missing = sorted(emergency_rules - covered)

    return check(
        "every emergency rule has a first-aid script",
        not missing,
        f"no script for {missing} — the avatar would read the escalation and "
        f"then have nothing to say about what to do",
    )


# --------------------------------------------------------------------------
# The rest of the corpus must not move
# --------------------------------------------------------------------------
# Mismatches the engine is already known to have on the opening sentence alone.
# The OCC-* vignettes are occult presentations that are *designed* to need
# follow-up questions, so they are expected here; the other two carry a
# documented `known_engine_gap`. Pinned as an exact set rather than a count so
# that fixing one and breaking another does not net out to "no change".
KNOWN_OPENING_MISMATCHES = {
    "BENIGN-017",
    "NEG-009",
    "OCC-001",
    "OCC-002",
    "OCC-003",
    "OCC-004",
    "OCC-005",
    "OCC-006",
    "OCC-007",
    "OCC-008",
}


def lexicon_changes_did_not_regress_the_corpus() -> list[str]:
    mismatched: set[str] = set()
    for vignette in vignettes.VIGNETTES:
        demographics = vignette.demographics or {}
        result = _screen(
            vignette.opening,
            age=demographics.get("age"),
            sex=demographics.get("sex"),
            is_pregnant=bool(demographics.get("is_pregnant")),
            is_postpartum=bool(demographics.get("is_postpartum")),
            pregnancy_week=demographics.get("pregnancy_week"),
            chronic_conditions=demographics.get("chronic") or [],
        )
        if str(result.urgency) != vignette.expected_urgency:
            mismatched.add(vignette.id)

    newly_broken = sorted(mismatched - KNOWN_OPENING_MISMATCHES)
    newly_fixed = sorted(KNOWN_OPENING_MISMATCHES - mismatched)

    failures = check(
        "no vignette regressed on the opening sentence",
        not newly_broken,
        f"newly failing: {newly_broken}",
    )
    # A fix is good news, but the pinned set has to be updated to match or this
    # stops being a regression test.
    failures += check(
        "the known-mismatch list is still accurate",
        not newly_fixed,
        f"now passing, remove from KNOWN_OPENING_MISMATCHES: {newly_fixed}",
    )
    return failures


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
SUITES = (
    ("Spoken emergencies are heard", spoken_emergencies_are_heard),
    ("Everyday speech is left alone", everyday_speech_is_left_alone),
    ("Denials still deny", denials_still_deny),
    ("Screening is pure", screening_is_pure),
    ("Emergency rules have first aid", every_emergency_rule_has_first_aid),
    ("Corpus did not regress", lexicon_changes_did_not_regress_the_corpus),
)


def main() -> int:
    print("=" * 74)
    print("HANDS-FREE EMERGENCY SCREENING")
    print("=" * 74)

    problems: list[str] = []
    for title, suite in SUITES:
        print(f"\n{title}")
        print("-" * 74)
        found = suite()
        problems += found
        for line in found:
            print(f"  FAIL  {line}")
        if not found:
            print("  all clear")

    print("\n" + "=" * 74)
    print(f"  Passed: {len(PASSED)}")
    print(f"  Failed: {len(FAILED)}")
    for line in problems:
        print(f"    - {line}")
    return 1 if problems else 0


def test_spoken_emergencies_are_heard() -> None:
    assert spoken_emergencies_are_heard() == []


def test_everyday_speech_is_left_alone() -> None:
    assert everyday_speech_is_left_alone() == []


def test_denials_still_deny() -> None:
    assert denials_still_deny() == []


def test_screening_is_pure() -> None:
    assert screening_is_pure() == []


def test_every_emergency_rule_has_first_aid() -> None:
    assert every_emergency_rule_has_first_aid() == []


def test_lexicon_changes_did_not_regress_the_corpus() -> None:
    assert lexicon_changes_did_not_regress_the_corpus() == []


if __name__ == "__main__":
    raise SystemExit(main())
