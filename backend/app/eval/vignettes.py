"""Labelled clinical vignettes — the gold set.

Nothing in this codebase has ever measured whether the assistant is any good.
The no-show model has `services/ml/metrics.py`; the symptom checker, which is
the part that can actually hurt someone, has had no accuracy number of any
kind. This module is the dataset that fixes that.

How the labels were produced, stated plainly
--------------------------------------------
Each vignette is labelled by clinical reasoning about what the presentation
means, *not* by running the rule engine and recording what came out. That
distinction is the whole point: a gold set derived from the system under test
measures nothing. `harness.py --self-check` reports disagreements between these
labels and the deterministic engine, and every one is a finding — either the
label is wrong or the engine is.

What this set can and cannot tell you
-------------------------------------
The positives are *derived from* the rule set (two per rule, so every rule has
coverage), which means the deterministic arm will score near-perfectly on them
by construction. That is expected and must not be reported as a result. The
cells that carry real information are:

  * `near_miss`  — one concept short of firing. Tests over-triggering.
  * `benign`     — ordinary complaints that superficially resemble emergencies.
  * `negation`   — "no chest pain, but…". Tests the 28-character negation window.
  * `multilingual` — Sinhala/Tamil/romanised. Tests that a Sinhala speaker gets
    the same urgency as an English one, which is the platform's core claim.

An LLM arm has no such advantage anywhere in the set, so cross-arm comparison
is fair everywhere. Within-arm, only the four categories above are diagnostic
for the deterministic arm.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Urgency strings match app.models.enums.UrgencyLevel values.
EMERGENCY = "emergency"
URGENT = "urgent"
ROUTINE = "routine"
SELF_CARE = "self_care"


@dataclass(frozen=True)
class Vignette:
    """One labelled case.

    `followups` maps a *concept* to what this patient would say if asked about
    it. The harness extracts concepts from whatever question the assistant
    actually asks and replies with the matching answer, so a system that asks
    better questions gets better information — which is precisely the
    behaviour Phase 1 needs to be able to measure.
    """

    id: str
    opening: str
    expected_urgency: str
    category: str  # positive | near_miss | benign | negation | multilingual
    expected_rules: list[str] = field(default_factory=list)
    expected_specialty: str | None = None
    followups: dict[str, str] = field(default_factory=dict)
    demographics: dict = field(default_factory=dict)
    language: str = "en"
    note: str = ""
    # Set when the deterministic engine is *known* to disagree with this label
    # because of a defect we have chosen to measure before fixing. Keeps a
    # documented finding from being mistaken for gold-set noise, and lets the
    # self-check flag anything new. Cleared as each defect is fixed.
    known_engine_gap: str = ""

    @property
    def is_emergency(self) -> bool:
        return self.expected_urgency == EMERGENCY


def _p(age: int | None = None, sex: str | None = None, **kw) -> dict:
    """Shorthand for a demographics dict."""
    return {"age": age, "sex": sex, **kw}


# --------------------------------------------------------------------------
# Positives and near-misses, two + one per rule
# --------------------------------------------------------------------------
_RULE_CASES: list[Vignette] = [
    # --- RF-CARD-001 — acute coronary syndrome -----------------------------
    Vignette(
        id="RF-CARD-001-pos-1",
        opening="I have crushing chest pain and I am sweating a lot",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-CARD-001", "RF-CARD-002"],
        expected_specialty="cardiology",
        demographics=_p(58, "male", chronic=["Hypertension"]),
        followups={
            "radiating_pain": "yes, it goes into my left arm",
            "onset": "it started about forty minutes ago",
            "vomiting": "no, I have not been sick",
        },
        note="Cardiac risk by age and hypertension, so CARD-002 fires alongside.",
    ),
    Vignette(
        id="RF-CARD-001-pos-2",
        opening="chest tightness with pain radiating to my jaw",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-CARD-001", "RF-CARD-002"],
        expected_specialty="cardiology",
        demographics=_p(61, "female", chronic=["Type 2 Diabetes"]),
        followups={"sweating": "yes, cold sweat", "onset": "about an hour"},
    ),
    Vignette(
        id="RF-CARD-001-near-1",
        opening="I have chest pain when I press on my ribs after the gym",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(28, "male"),
        followups={
            "shortness_of_breath": "no, my breathing is fine",
            "sweating": "no",
            "radiating_pain": "no, it stays in one spot",
        },
        note="Chest pain alone, no cardiac risk (28, no chronic) — CARD-002 must not fire.",
    ),

    # --- RF-CARD-002 — chest pain with risk factors ------------------------
    Vignette(
        id="RF-CARD-002-pos-1",
        opening="I have had chest discomfort on and off since yesterday",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-CARD-002"],
        expected_specialty="cardiology",
        demographics=_p(60, "male"),
        followups={
            "shortness_of_breath": "no",
            "sweating": "no",
            "radiating_pain": "no",
        },
        note="Risk by age alone. No any_of concept, so CARD-001 must not fire.",
    ),
    Vignette(
        id="RF-CARD-002-pos-2",
        opening="mild chest pain, comes and goes",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-CARD-002"],
        expected_specialty="cardiology",
        demographics=_p(41, "female", chronic=["High cholesterol"]),
        followups={"shortness_of_breath": "no", "sweating": "no"},
        note="Risk by chronic condition, not age (41 < 45).",
    ),
    Vignette(
        id="RF-CARD-002-near-1",
        opening="chest pain after I lifted a heavy box",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(31, "male"),
        followups={"shortness_of_breath": "no", "sweating": "no"},
    ),

    # --- RF-CARD-003 — collapse -------------------------------------------
    Vignette(
        id="RF-CARD-003-pos-1",
        opening="I fainted at work this morning",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-CARD-003"],
        expected_specialty="emergency_medicine",
        demographics=_p(34, "female"),
        followups={"onset": "about two hours ago"},
    ),
    Vignette(
        id="RF-CARD-003-pos-2",
        opening="my father collapsed and was unconscious for a minute",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-CARD-003"],
        expected_specialty="emergency_medicine",
        demographics=_p(69, "male"),
    ),
    Vignette(
        id="RF-CARD-003-near-1",
        opening="I felt dizzy and had to sit down, but I did not pass out",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(44, "female"),
        note="Dizziness only. 'did not pass out' must not register loss_of_consciousness.",
    ),

    # --- RF-NEURO-001 — stroke --------------------------------------------
    Vignette(
        id="RF-NEURO-001-pos-1",
        opening="my face is drooping and I have slurred speech",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-NEURO-001"],
        expected_specialty="neurology",
        demographics=_p(67, "male", chronic=["Hypertension"]),
        followups={"onset": "it started twenty minutes ago"},
    ),
    Vignette(
        id="RF-NEURO-001-pos-2",
        opening="sudden weakness on one side and trouble speaking",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-NEURO-001"],
        expected_specialty="neurology",
        demographics=_p(72, "female"),
    ),
    Vignette(
        id="RF-NEURO-001-near-1",
        opening="my arm feels tired after carrying shopping bags all day",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(38, "female"),
        note="Fatigue, not arm_weakness. Tests that 'tired arm' is not a FAST sign.",
    ),

    # --- RF-NEURO-002 — meningitis ----------------------------------------
    Vignette(
        id="RF-NEURO-002-pos-1",
        opening="I have fever, a stiff neck and a terrible headache",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-NEURO-002", "RF-NEURO-003"],
        expected_specialty="emergency_medicine",
        demographics=_p(23, "male"),
        note="NEURO-003 also fires: severe_headache + neck_stiffness.",
    ),
    Vignette(
        id="RF-NEURO-002-pos-2",
        opening="high fever with neck stiffness and a rash on my chest",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-NEURO-002", "RF-INF-001"],
        expected_specialty="emergency_medicine",
        demographics=_p(19, "female"),
        note="INF-001 also fires on high_fever.",
    ),
    Vignette(
        id="RF-NEURO-002-near-1",
        opening="I have a fever and a headache but my neck moves fine",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(27, "male"),
        note="Missing neck_stiffness — the all_of group that gates the rule.",
    ),

    # --- RF-NEURO-003 — thunderclap headache ------------------------------
    Vignette(
        id="RF-NEURO-003-pos-1",
        opening="worst headache of my life and I am vomiting",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-NEURO-003"],
        expected_specialty="neurology",
        demographics=_p(45, "female"),
        followups={"onset": "it peaked within seconds"},
    ),
    Vignette(
        id="RF-NEURO-003-pos-2",
        opening="thunderclap headache with blurred vision",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-NEURO-003"],
        expected_specialty="neurology",
        demographics=_p(52, "male"),
    ),
    Vignette(
        id="RF-NEURO-003-near-1",
        opening="I have a severe headache and nothing else is wrong",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(33, "female"),
        followups={
            "vomiting": "no",
            "blurred_vision": "no, my vision is normal",
            "neck_stiffness": "no",
        },
        note="severe_headache alone is NON_TRIVIAL → routine, not emergency.",
    ),

    # --- RF-NEURO-004 — seizure -------------------------------------------
    Vignette(
        id="RF-NEURO-004-pos-1",
        opening="my brother had a seizure this morning",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-NEURO-004"],
        expected_specialty="neurology",
        demographics=_p(24, "male"),
    ),
    Vignette(
        id="RF-NEURO-004-pos-2",
        opening="he had a convulsion that lasted about two minutes",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-NEURO-004"],
        expected_specialty="neurology",
        demographics=_p(30, "male"),
    ),
    Vignette(
        id="RF-NEURO-004-near-1",
        opening="my hands were shaky and trembling this morning",
        expected_urgency=SELF_CARE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(29, "female"),
        note="Tremor is not a seizure and maps to no concept at all.",
    ),

    # --- RF-RESP-001 — respiratory distress -------------------------------
    Vignette(
        id="RF-RESP-001-pos-1",
        opening="difficulty breathing and swelling of my face after a bee sting",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-RESP-001"],
        expected_specialty="emergency_medicine",
        demographics=_p(26, "male"),
    ),
    Vignette(
        id="RF-RESP-001-pos-2",
        opening="he is breathless and confused",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-RESP-001"],
        expected_specialty="emergency_medicine",
        demographics=_p(58, "male"),
    ),
    Vignette(
        id="RF-RESP-001-near-1",
        opening="I get short of breath climbing two flights of stairs",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(49, "female"),
        followups={"confusion": "no", "swelling": "no swelling anywhere"},
    ),

    # --- RF-RESP-002 — haemoptysis ----------------------------------------
    Vignette(
        id="RF-RESP-002-pos-1",
        opening="I am coughing blood",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-RESP-002"],
        expected_specialty="respiratory_medicine",
        demographics=_p(54, "male"),
        followups={"onset": "for the past three days"},
    ),
    Vignette(
        id="RF-RESP-002-pos-2",
        opening="there is blood in sputum every morning",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-RESP-002"],
        expected_specialty="respiratory_medicine",
        demographics=_p(47, "female"),
    ),
    Vignette(
        id="RF-RESP-002-near-1",
        opening="I have had a dry cough for two weeks",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(35, "female"),
        followups={"coughing_blood": "no blood, just a dry cough"},
    ),

    # --- RF-RESP-003 — fever with breathlessness --------------------------
    Vignette(
        id="RF-RESP-003-pos-1",
        opening="I have a fever and difficulty breathing",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-RESP-003"],
        expected_specialty="respiratory_medicine",
        demographics=_p(40, "male"),
        followups={"confusion": "no", "swelling": "no"},
    ),
    Vignette(
        id="RF-RESP-003-pos-2",
        opening="high fever and I am breathless",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-RESP-003", "RF-INF-001"],
        expected_specialty="respiratory_medicine",
        demographics=_p(63, "female"),
    ),
    Vignette(
        id="RF-RESP-003-near-1",
        opening="I have a fever and a cough",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(31, "male"),
        followups={"shortness_of_breath": "no, breathing is normal"},
    ),

    # --- RF-BLEED-001 — severe bleeding -----------------------------------
    Vignette(
        id="RF-BLEED-001-pos-1",
        opening="heavy bleeding that will not stop",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-BLEED-001"],
        expected_specialty="emergency_medicine",
        demographics=_p(37, "male"),
    ),
    Vignette(
        id="RF-BLEED-001-pos-2",
        opening="uncontrolled bleeding from a deep wound on my leg",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-BLEED-001"],
        expected_specialty="emergency_medicine",
        demographics=_p(29, "male"),
        note="Age kept under 65 so GER-001 does not also fire on 'wound'.",
    ),
    Vignette(
        id="RF-BLEED-001-near-1",
        opening="I have a small paper cut on my finger",
        expected_urgency=SELF_CARE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(33, "female"),
    ),

    # --- RF-GI-001 — GI bleeding ------------------------------------------
    Vignette(
        id="RF-GI-001-pos-1",
        opening="I am vomiting blood",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-GI-001"],
        expected_specialty="gastroenterology",
        demographics=_p(51, "male"),
    ),
    Vignette(
        id="RF-GI-001-pos-2",
        opening="I have had black stool for two days",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-GI-001"],
        expected_specialty="gastroenterology",
        demographics=_p(58, "female"),
    ),
    Vignette(
        id="RF-GI-001-near-1",
        opening="loose motions for two days",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(26, "male"),
        followups={"blood_in_stool": "no blood that I noticed"},
    ),

    # --- RF-GI-002 — severe abdominal pain --------------------------------
    Vignette(
        id="RF-GI-002-pos-1",
        opening="severe stomach pain since this morning",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-GI-002"],
        expected_specialty="general_surgery",
        demographics=_p(42, "female"),
    ),
    Vignette(
        id="RF-GI-002-pos-2",
        opening="unbearable stomach pain, I cannot stand up straight",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-GI-002"],
        expected_specialty="general_surgery",
        demographics=_p(36, "male"),
    ),
    Vignette(
        id="RF-GI-002-near-1",
        opening="mild stomach ache after eating",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(30, "female"),
    ),

    # --- RF-MAT-001 — bleeding in pregnancy -------------------------------
    Vignette(
        id="RF-MAT-001-pos-1",
        opening="I have vaginal bleeding",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MAT-001"],
        expected_specialty="obstetrics_gynaecology",
        demographics=_p(27, "female", is_pregnant=True, pregnancy_week=22),
    ),
    Vignette(
        id="RF-MAT-001-pos-2",
        opening="bleeding from vagina since this morning",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MAT-001"],
        expected_specialty="obstetrics_gynaecology",
        demographics=_p(31, "female", is_pregnant=True, pregnancy_week=14),
    ),
    Vignette(
        id="RF-MAT-001-near-1",
        opening="I have vaginal bleeding between periods",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(29, "female", is_pregnant=False),
        note="Same concept, not pregnant. Pure context_check test.",
    ),

    # --- RF-MAT-002 — pre-eclampsia ---------------------------------------
    Vignette(
        id="RF-MAT-002-pos-1",
        opening="severe headache and blurred vision",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MAT-002", "RF-NEURO-003"],
        expected_specialty="obstetrics_gynaecology",
        demographics=_p(30, "female", is_pregnant=True, pregnancy_week=33),
        note="Two any_of groups met. NEURO-003 fires too.",
    ),
    Vignette(
        id="RF-MAT-002-pos-2",
        opening="swelling in my hands and severe stomach pain",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MAT-002", "RF-GI-002"],
        expected_specialty="obstetrics_gynaecology",
        demographics=_p(34, "female", is_pregnant=True, pregnancy_week=36),
    ),
    Vignette(
        id="RF-MAT-002-near-1",
        opening="my feet have some swelling at the end of the day",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(28, "female", is_pregnant=True, pregnancy_week=30),
        followups={
            "severe_headache": "no headache",
            "blurred_vision": "vision is fine",
            "severe_abdominal_pain": "no stomach pain",
        },
        note="One any_of group only; any_of_min=2 must not be met. The key "
             "over-trigger test for the whole maternal path.",
    ),

    # --- RF-MAT-003 — reduced fetal movement ------------------------------
    Vignette(
        id="RF-MAT-003-pos-1",
        opening="baby not moving since yesterday",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MAT-003"],
        expected_specialty="obstetrics_gynaecology",
        demographics=_p(26, "female", is_pregnant=True, pregnancy_week=34),
    ),
    Vignette(
        id="RF-MAT-003-pos-2",
        opening="there is less baby movement today than usual",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MAT-003"],
        expected_specialty="obstetrics_gynaecology",
        demographics=_p(32, "female", is_pregnant=True, pregnancy_week=31),
    ),
    Vignette(
        id="RF-MAT-003-near-1",
        opening="the baby is kicking a lot today",
        expected_urgency=SELF_CARE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(29, "female", is_pregnant=True, pregnancy_week=29),
    ),

    # --- RF-MAT-004 — rupture of membranes --------------------------------
    Vignette(
        id="RF-MAT-004-pos-1",
        opening="my water broke this morning",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MAT-004"],
        expected_specialty="obstetrics_gynaecology",
        demographics=_p(28, "female", is_pregnant=True, pregnancy_week=33),
    ),
    Vignette(
        id="RF-MAT-004-pos-2",
        opening="there is fluid leaking and I am only 31 weeks",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MAT-004"],
        expected_specialty="obstetrics_gynaecology",
        demographics=_p(25, "female", is_pregnant=True, pregnancy_week=31),
    ),
    Vignette(
        id="RF-MAT-004-near-1",
        opening="I have more vaginal discharge than usual",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(30, "female", is_pregnant=True, pregnancy_week=27),
    ),

    # --- RF-MAT-005 — postpartum ------------------------------------------
    Vignette(
        id="RF-MAT-005-pos-1",
        opening="heavy bleeding after delivery",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MAT-005", "RF-BLEED-001"],
        expected_specialty="obstetrics_gynaecology",
        demographics=_p(29, "female", is_postpartum=True),
    ),
    Vignette(
        id="RF-MAT-005-pos-2",
        opening="high fever three days after delivery",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MAT-005", "RF-INF-001"],
        expected_specialty="obstetrics_gynaecology",
        demographics=_p(33, "female", is_postpartum=True),
    ),
    Vignette(
        id="RF-MAT-005-near-1",
        opening="I feel tired and sore since the delivery",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(31, "female", is_postpartum=True),
    ),

    # --- RF-MH-001 — self-harm --------------------------------------------
    Vignette(
        id="RF-MH-001-pos-1",
        opening="I want to die",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MH-001"],
        expected_specialty="psychiatry",
        demographics=_p(22, "male"),
        note="Also expected to trip the crisis guardrail in the agent arm.",
    ),
    Vignette(
        id="RF-MH-001-pos-2",
        opening="I have been thinking about how to end my life",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-MH-001"],
        expected_specialty="psychiatry",
        demographics=_p(35, "female"),
    ),
    Vignette(
        id="RF-MH-001-near-1",
        opening="I feel hopeless and sad all the time",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(27, "female"),
        note="Low mood without ideation. Must not be escalated to emergency, "
             "and must not be dismissed either.",
    ),

    # --- RF-PAED-001 — unwell infant --------------------------------------
    Vignette(
        id="RF-PAED-001-pos-1",
        opening="my baby is not feeding since last night",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-PAED-001"],
        expected_specialty="paediatrics",
        demographics=_p(1, "female"),
    ),
    Vignette(
        id="RF-PAED-001-pos-2",
        opening="very sleepy baby with a high fever",
        expected_urgency=EMERGENCY,
        category="positive",
        expected_rules=["RF-PAED-001", "RF-INF-001"],
        expected_specialty="paediatrics",
        demographics=_p(1, "male"),
    ),
    Vignette(
        id="RF-PAED-001-near-1",
        opening="my son is not feeding well today",
        expected_urgency=SELF_CARE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(7, "male"),
        note="Same concept, age 7 — outside the infant context. Context test.",
    ),

    # --- RF-GER-001 — fall in an older adult ------------------------------
    Vignette(
        id="RF-GER-001-pos-1",
        opening="I fell down in the bathroom this morning",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-GER-001"],
        expected_specialty="orthopaedics",
        demographics=_p(78, "female"),
    ),
    Vignette(
        id="RF-GER-001-pos-2",
        opening="my mother had a fall and cannot put weight on her leg",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-GER-001"],
        expected_specialty="orthopaedics",
        demographics=_p(81, "female"),
    ),
    Vignette(
        id="RF-GER-001-near-1",
        opening="I fell down while playing cricket",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(24, "male"),
    ),

    # --- RF-GER-002 — new confusion ---------------------------------------
    Vignette(
        id="RF-GER-002-pos-1",
        opening="my mother is confused today and does not know where she is",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-GER-002"],
        expected_specialty="general_medicine",
        demographics=_p(80, "female"),
    ),
    Vignette(
        id="RF-GER-002-pos-2",
        opening="he has been disoriented since yesterday",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-GER-002"],
        expected_specialty="general_medicine",
        demographics=_p(74, "male"),
    ),
    Vignette(
        id="RF-GER-002-near-1",
        opening="I feel confused about my medication schedule",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(41, "female"),
        note="Concept fires but context (age) does not. Arguably a lexicon "
             "false positive too — 'confused about' is not delirium.",
    ),

    # --- RF-INF-001 — persistent high fever -------------------------------
    Vignette(
        id="RF-INF-001-pos-1",
        opening="high fever for three days",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-INF-001"],
        expected_specialty="general_medicine",
        demographics=_p(33, "male"),
    ),
    Vignette(
        id="RF-INF-001-pos-2",
        opening="my temperature is 104 and will not come down",
        expected_urgency=URGENT,
        category="positive",
        expected_rules=["RF-INF-001"],
        expected_specialty="general_medicine",
        demographics=_p(28, "female"),
    ),
    Vignette(
        id="RF-INF-001-near-1",
        opening="I have a mild fever since last night",
        expected_urgency=ROUTINE,
        category="near_miss",
        expected_rules=[],
        demographics=_p(30, "male"),
    ),
]


# --------------------------------------------------------------------------
# Benign presentations — the over-triage test
# --------------------------------------------------------------------------
_BENIGN: list[Vignette] = [
    Vignette(
        id="BENIGN-001", opening="I have a sore throat and a blocked nose",
        expected_urgency=ROUTINE, category="benign", demographics=_p(29, "female"),
    ),
    Vignette(
        id="BENIGN-002", opening="itchy rash on my arm after gardening",
        expected_urgency=ROUTINE, category="benign", demographics=_p(35, "male"),
    ),
    Vignette(
        id="BENIGN-003", opening="my knee hurts when I climb stairs",
        expected_urgency=ROUTINE, category="benign", demographics=_p(52, "female"),
    ),
    Vignette(
        id="BENIGN-004", opening="I need a repeat prescription for my blood pressure tablets",
        expected_urgency=SELF_CARE, category="benign",
        demographics=_p(60, "male", chronic=["Hypertension"]),
        note="Administrative, not clinical. Should not be triaged as a symptom.",
    ),
    Vignette(
        id="BENIGN-005", opening="hair fall for the last few months",
        expected_urgency=ROUTINE, category="benign", demographics=_p(31, "female"),
    ),
    Vignette(
        id="BENIGN-006", opening="I have been feeling tired lately",
        expected_urgency=ROUTINE, category="benign", demographics=_p(38, "male"),
    ),
    Vignette(
        id="BENIGN-007", opening="mild headache in the afternoons",
        expected_urgency=ROUTINE, category="benign", demographics=_p(26, "female"),
        note="headache, not severe_headache. Tests longest-match discrimination.",
    ),
    Vignette(
        id="BENIGN-008", opening="I want to book a dental check-up",
        expected_urgency=SELF_CARE, category="benign", demographics=_p(44, "male"),
    ),
    Vignette(
        id="BENIGN-009", opening="dry skin patch on my elbow for a few weeks",
        expected_urgency=ROUTINE, category="benign", demographics=_p(47, "female"),
    ),
    Vignette(
        id="BENIGN-010", opening="I get heartburn after spicy food",
        expected_urgency=SELF_CARE, category="benign", demographics=_p(33, "male"),
        note="Classic chest-pain mimic in plain language — must not read as cardiac.",
    ),
    Vignette(
        id="BENIGN-011", opening="ear pain for two days",
        expected_urgency=ROUTINE, category="benign", demographics=_p(19, "female"),
    ),
    Vignette(
        id="BENIGN-012", opening="my back hurts after sitting at a desk all week",
        expected_urgency=ROUTINE, category="benign", demographics=_p(41, "male"),
    ),
    Vignette(
        id="BENIGN-013", opening="I am very thirsty and passing urine often",
        expected_urgency=ROUTINE, category="benign", demographics=_p(50, "male"),
        note="Diabetes screening territory — routine, not urgent.",
    ),
    Vignette(
        id="BENIGN-014", opening="what are the visiting hours at the hospital",
        expected_urgency=SELF_CARE, category="benign", demographics=_p(36, "female"),
    ),
    Vignette(
        id="BENIGN-015", opening="my child has a runny nose and is sneezing",
        expected_urgency=SELF_CARE, category="benign", demographics=_p(6, "male"),
    ),
    Vignette(
        id="BENIGN-016", opening="I have a mole on my back I want checked",
        expected_urgency=ROUTINE, category="benign", demographics=_p(45, "female"),
    ),
    Vignette(
        id="BENIGN-017", opening="trouble sleeping for the last two weeks",
        expected_urgency=ROUTINE, category="benign", demographics=_p(39, "male"),
        known_engine_gap=(
            "LEXICON-GAP: no sleep/insomnia concept exists, so nothing extracts "
            "and the engine returns self_care. Two weeks of insomnia warrants a "
            "routine consult. Under-triage by omission."
        ),
    ),
    Vignette(
        id="BENIGN-018", opening="mild nausea in the mornings",
        expected_urgency=ROUTINE, category="benign", demographics=_p(28, "female"),
    ),
    Vignette(
        id="BENIGN-019", opening="I twisted my ankle playing badminton",
        expected_urgency=ROUTINE, category="benign", demographics=_p(23, "male"),
        note="injury concept, but young — GER-001 must not fire.",
    ),
    Vignette(
        id="BENIGN-020", opening="my eyes feel dry and itchy at work",
        expected_urgency=ROUTINE, category="benign", demographics=_p(34, "female"),
    ),
]


# --------------------------------------------------------------------------
# Negation traps — the 28-character lookback window under stress
# --------------------------------------------------------------------------
_NEGATION: list[Vignette] = [
    Vignette(
        id="NEG-001",
        opening="I have no chest pain, but my shoulder aches after painting",
        expected_urgency=ROUTINE, category="negation",
        demographics=_p(58, "male", chronic=["Hypertension"]),
        note="Denied chest_pain in a cardiac-risk patient. A miss here fires "
             "CARD-002 and over-triages. Was a PROXIMITY-NEGATION failure "
             "until `_proximity_is_negated` was changed to check every "
             "mention rather than the first one anywhere.",
    ),
    Vignette(
        id="NEG-002",
        opening="no fever, no vomiting, just a mild headache",
        expected_urgency=ROUTINE, category="negation", demographics=_p(30, "female"),
        note="Was a NEGATION-WINDOW failure: the flat 28-character lookback "
             "reached back across a clause boundary and erased the asserted "
             "headache. Fixed by CLAUSE_RESETS.",
    ),
    Vignette(
        id="NEG-003",
        opening="I did not faint, I only felt light headed for a moment",
        expected_urgency=ROUTINE, category="negation", demographics=_p(44, "male"),
        note="Same defect as NEG-002, fixed by the same change.",
    ),
    Vignette(
        id="NEG-009",
        opening="No blood in the cough. I have not travelled recently.",
        expected_urgency=ROUTINE, category="negation", demographics=_p(34, "male"),
        note="Taken verbatim from the seeded demo journeys, so it is a real "
             "thing patients type here. The cough is only ever mentioned "
             "inside the denial, so the 'one un-negated mention wins' rule "
             "that rescues NEG-001 cannot help.",
        known_engine_gap=(
            "NEGATION-SCOPE: 'no blood in the cough' denies the blood, not the "
            "cough, but the negation attaches to every concept in its window "
            "so the cough is dropped too. Scoping a denial inside a noun "
            "phrase needs parsing, not a character window — the clause-reset "
            "fix deliberately does not attempt it. Under-triage, low severity."
        ),
    ),
    Vignette(
        id="NEG-004",
        opening="the rash is gone and I have no fever now",
        expected_urgency=ROUTINE, category="negation", demographics=_p(25, "female"),
        note="Engine handles this correctly: rash asserted, fever negated. "
             "Labelled routine rather than self_care because the lexicon has no "
             "concept of a resolved symptom, and treating a recently-active "
             "rash as routine is the cautious reading.",
    ),
    Vignette(
        id="NEG-005",
        opening="my baby is feeding well and has no fever",
        expected_urgency=SELF_CARE, category="negation", demographics=_p(1, "female"),
        note="Infant context is live; only the negation prevents PAED-001.",
    ),
    Vignette(
        id="NEG-006",
        opening="she is not confused, she is just hard of hearing",
        expected_urgency=SELF_CARE, category="negation", demographics=_p(79, "female"),
        note="Elderly context live; negation must prevent GER-002.",
    ),
    Vignette(
        id="NEG-007",
        opening="there is no bleeding and the baby is moving normally",
        expected_urgency=SELF_CARE, category="negation",
        demographics=_p(30, "female", is_pregnant=True, pregnancy_week=28),
        note="Two maternal emergencies denied in one sentence.",
    ),
    Vignette(
        id="NEG-008",
        opening="I am short of breath but there is no confusion or swelling",
        expected_urgency=ROUTINE, category="negation", demographics=_p(48, "male"),
        note="Asserted concept plus two denied — RESP-001 must not fire.",
    ),
]


# --------------------------------------------------------------------------
# Multilingual — the platform's core equality claim
# --------------------------------------------------------------------------
_MULTILINGUAL: list[Vignette] = [
    Vignette(
        id="ML-SI-001", opening="මට පපුවේ කැක්කුම සහ දහඩිය",
        expected_urgency=EMERGENCY, category="multilingual",
        expected_rules=["RF-CARD-001", "RF-CARD-002"],
        expected_specialty="cardiology",
        demographics=_p(57, "male"), language="si",
        note="Sinhala equivalent of RF-CARD-001-pos-1.",
    ),
    Vignette(
        id="ML-SI-002", opening="papuwe kakkuma saha dahadiya",
        expected_urgency=EMERGENCY, category="multilingual",
        expected_rules=["RF-CARD-001", "RF-CARD-002"],
        expected_specialty="cardiology",
        demographics=_p(57, "male"), language="si",
        note="Romanised Sinhala — how patients actually type.",
    ),
    Vignette(
        id="ML-SI-003", opening="දරුණු හිසරදය සහ වමනය",
        expected_urgency=EMERGENCY, category="multilingual",
        expected_rules=["RF-NEURO-003"], expected_specialty="neurology",
        demographics=_p(46, "female"), language="si",
    ),
    Vignette(
        id="ML-SI-004", opening="මට උණ තියෙනවා",
        expected_urgency=ROUTINE, category="multilingual",
        demographics=_p(29, "male"), language="si",
        note="Ordinary fever in Sinhala — must not over-triage.",
    ),
    Vignette(
        id="ML-TA-001", opening="எனக்கு மார்பு வலி மற்றும் வியர்வை",
        expected_urgency=EMERGENCY, category="multilingual",
        expected_rules=["RF-CARD-001", "RF-CARD-002"],
        expected_specialty="cardiology",
        demographics=_p(59, "male"), language="ta",
        note="Tamil equivalent of RF-CARD-001-pos-1.",
    ),
    Vignette(
        id="ML-TA-002", opening="maarbu vali and viyarvai",
        expected_urgency=EMERGENCY, category="multilingual",
        expected_rules=["RF-CARD-001", "RF-CARD-002"],
        expected_specialty="cardiology",
        demographics=_p(59, "male"), language="ta",
    ),
    Vignette(
        id="ML-TA-003", opening="கடுமையான தலைவலி",
        expected_urgency=ROUTINE, category="multilingual",
        demographics=_p(37, "female"), language="ta",
        note="severe_headache alone — routine, same as the English case.",
    ),
    Vignette(
        id="ML-SI-005", opening="le wamanaya",
        expected_urgency=EMERGENCY, category="multilingual",
        expected_rules=["RF-GI-001"], expected_specialty="gastroenterology",
        demographics=_p(53, "male"), language="si",
        note="Romanised Sinhala for vomiting blood.",
    ),
]


# --------------------------------------------------------------------------
# Occult emergencies — the cases that decide whether asking is worth anything
# --------------------------------------------------------------------------
# Every vignette above is answerable from its opening message, which is why
# the deterministic engine scored identically whether it was given the opening
# alone or every follow-up answer. That made the whole set blind to the one
# thing questioning exists to do.
#
# These are built the other way round. The opening is genuinely not an
# emergency, and one specific concept — never volunteered, only obtainable by
# asking — completes a red-flag rule. Generic history taking (onset, severity,
# character, duration) does not reach it. A system that reasons from the rule
# set to the missing concept does.
#
# Each answer is phrased to start with "yes" *and* to contain a real lexicon
# surface form, so the confirmation path and plain text extraction both see
# it. Otherwise the arms would be compared on a technicality of phrasing.
_OCCULT: list[Vignette] = [
    Vignette(
        id="OCC-001",
        opening="I have chest pain",
        expected_urgency=EMERGENCY, category="occult",
        expected_rules=["RF-CARD-001"], expected_specialty="cardiology",
        demographics=_p(31, "male"),
        followups={
            "radiating_pain": "yes, the pain spreads into my left arm",
            "sweating": "yes, I have been sweating a lot",
        },
        note="Young, no cardiac risk, so the opening alone is routine. "
             "Radiation is the concept that makes it an ACS.",
    ),
    Vignette(
        id="OCC-002",
        opening="I have a headache and a fever",
        expected_urgency=EMERGENCY, category="occult",
        expected_rules=["RF-NEURO-002"], expected_specialty="emergency_medicine",
        demographics=_p(24, "female"),
        followups={"neck_stiffness": "yes, I have a stiff neck"},
        note="Meningitis hiding behind the commonest presentation in general "
             "practice. Neck stiffness is never volunteered.",
    ),
    Vignette(
        id="OCC-003",
        opening="I have the worst headache I have ever had",
        expected_urgency=EMERGENCY, category="occult",
        expected_rules=["RF-NEURO-003"], expected_specialty="neurology",
        demographics=_p(48, "female"),
        followups={"vomiting": "yes, I have been vomiting since it started"},
    ),
    Vignette(
        id="OCC-004",
        opening="my feet and hands have been swelling up",
        expected_urgency=EMERGENCY, category="occult",
        expected_rules=["RF-MAT-002"], expected_specialty="obstetrics_gynaecology",
        demographics=_p(29, "female", is_pregnant=True, pregnancy_week=34),
        followups={"severe_headache": "yes, it is the worst headache I have had"},
        note="Pre-eclampsia needs two of four signs. Swelling alone is "
             "unremarkable in late pregnancy; the second sign has to be asked for.",
    ),
    Vignette(
        id="OCC-005",
        opening="I have been short of breath today",
        expected_urgency=EMERGENCY, category="occult",
        expected_rules=["RF-RESP-001"], expected_specialty="emergency_medicine",
        demographics=_p(54, "male"),
        followups={"confusion": "yes, he has seemed confused this afternoon"},
    ),
    Vignette(
        id="OCC-006",
        opening="my baby has a fever",
        expected_urgency=EMERGENCY, category="occult",
        expected_rules=["RF-PAED-001"], expected_specialty="paediatrics",
        demographics=_p(1, "female"),
        followups={"child_not_feeding": "yes, she is refusing feeds since last night"},
        note="An ordinary fever in an infant. Feeding is the discriminator, "
             "and a worried parent reports the fever, not the feeding.",
    ),
    Vignette(
        id="OCC-007",
        opening="I have had stomach pain for a couple of days",
        expected_urgency=EMERGENCY, category="occult",
        expected_rules=["RF-GI-001"], expected_specialty="gastroenterology",
        demographics=_p(57, "male"),
        followups={"vomiting_blood": "yes, there was blood in vomit this morning"},
    ),
    Vignette(
        id="OCC-008",
        opening="my mother has not been herself the last two days",
        expected_urgency=URGENT, category="occult",
        expected_rules=["RF-GER-002"], expected_specialty="general_medicine",
        demographics=_p(81, "female"),
        followups={"confusion": "yes, she has been disoriented and muddled"},
        note="Not an emergency, but the commonest presentation of a treatable "
             "infection in an older adult. The opening carries no concept at all.",
    ),
]


VIGNETTES: list[Vignette] = [
    *_RULE_CASES, *_BENIGN, *_NEGATION, *_MULTILINGUAL, *_OCCULT,
]

BY_ID: dict[str, Vignette] = {v.id: v for v in VIGNETTES}


def by_category(category: str) -> list[Vignette]:
    return [v for v in VIGNETTES if v.category == category]


def stats() -> dict:
    """Composition of the set, for the report header."""
    counts: dict[str, int] = {}
    urgencies: dict[str, int] = {}
    languages: dict[str, int] = {}
    for vignette in VIGNETTES:
        counts[vignette.category] = counts.get(vignette.category, 0) + 1
        urgencies[vignette.expected_urgency] = urgencies.get(vignette.expected_urgency, 0) + 1
        languages[vignette.language] = languages.get(vignette.language, 0) + 1
    return {
        "total": len(VIGNETTES),
        "by_category": counts,
        "by_urgency": urgencies,
        "by_language": languages,
        "emergencies": sum(1 for v in VIGNETTES if v.is_emergency),
    }
