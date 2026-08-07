"""Deterministic clinical red-flag rules.

These rules — not the LLM — decide urgency (internal rule 1).  A rule fires on
*concepts* extracted by the lexicon, so it behaves identically across English,
Sinhala and Tamil.

Rule semantics
--------------
``all_of``   every concept group must match (each group is a set of
             alternatives, so ``{"chest_pain"}`` means that concept, and
             ``{"sweating", "nausea"}`` means either one).
``any_of``   at least ``any_of_min`` of these groups must match.
``context``  optional predicates over patient context (pregnancy, age, ...).

Rules are evaluated independently and the highest resulting urgency wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.models.enums import UrgencyLevel

# A patient-context dict: {"is_pregnant": bool, "age": int|None, "sex": str|None,
#                          "pregnancy_week": int|None, "chronic": set[str]}
Context = dict


@dataclass(frozen=True)
class RedFlagRule:
    id: str
    label: str
    urgency: UrgencyLevel
    rationale: str
    all_of: list[set[str]] = field(default_factory=list)
    any_of: list[set[str]] = field(default_factory=list)
    any_of_min: int = 1
    context_check: Callable[[Context], bool] | None = None
    # Capabilities the receiving facility must have if this rule fires.
    required_capabilities: list[str] = field(default_factory=list)
    specialty_code: str | None = None
    category: str = "general"

    def evaluate(self, concepts: set[str], context: Context) -> list[str] | None:
        """Return the matched concepts if the rule fires, else None."""
        if self.context_check and not self.context_check(context):
            return None

        matched: list[str] = []
        for group in self.all_of:
            hit = group & concepts
            if not hit:
                return None
            matched.extend(sorted(hit))

        if self.any_of:
            satisfied = [g for g in self.any_of if g & concepts]
            if len(satisfied) < self.any_of_min:
                return None
            for group in satisfied:
                matched.extend(sorted(group & concepts))

        return sorted(set(matched))


def _is_pregnant(ctx: Context) -> bool:
    return bool(ctx.get("is_pregnant"))


def _is_postpartum(ctx: Context) -> bool:
    return bool(ctx.get("is_postpartum"))


def _is_elderly(ctx: Context) -> bool:
    age = ctx.get("age")
    return age is not None and age >= 65


def _is_infant(ctx: Context) -> bool:
    age = ctx.get("age")
    return age is not None and age <= 2


def _cardiac_risk(ctx: Context) -> bool:
    """Known cardiac history or age that raises baseline cardiac risk."""
    chronic = {c.lower() for c in ctx.get("chronic", set())}
    cardiac_terms = ("heart", "cardiac", "angina", "hypertension", "cholesterol", "diabetes")
    if any(term in c for c in chronic for term in cardiac_terms):
        return True
    age = ctx.get("age")
    return age is not None and age >= 45


# --------------------------------------------------------------------------
# EMERGENCY rules
# --------------------------------------------------------------------------
RULES: list[RedFlagRule] = [
    # --- Cardiac ---
    RedFlagRule(
        id="RF-CARD-001",
        label="Possible acute coronary syndrome",
        urgency=UrgencyLevel.EMERGENCY,
        category="cardiac",
        all_of=[{"chest_pain"}],
        any_of=[{"shortness_of_breath"}, {"sweating"}, {"radiating_pain"}, {"vomiting"}],
        any_of_min=1,
        rationale=(
            "Chest pain occurring together with breathlessness, sweating or pain "
            "spreading to the arm or jaw is a recognised warning pattern for a "
            "heart attack and needs immediate assessment."
        ),
        required_capabilities=["emergency", "cardiology", "ecg"],
        specialty_code="cardiology",
    ),
    RedFlagRule(
        id="RF-CARD-002",
        label="Chest pain with cardiac risk factors",
        urgency=UrgencyLevel.URGENT,
        category="cardiac",
        all_of=[{"chest_pain"}],
        context_check=_cardiac_risk,
        rationale=(
            "Chest pain in a person with existing cardiac risk factors requires "
            "prompt medical assessment even without other warning signs."
        ),
        required_capabilities=["cardiology", "ecg"],
        specialty_code="cardiology",
    ),
    RedFlagRule(
        id="RF-CARD-003",
        label="Collapse or loss of consciousness",
        urgency=UrgencyLevel.EMERGENCY,
        category="cardiac",
        all_of=[{"loss_of_consciousness"}],
        rationale=(
            "Fainting or loss of consciousness can indicate a serious cardiac, "
            "neurological or metabolic cause and needs urgent evaluation."
        ),
        required_capabilities=["emergency"],
        specialty_code="emergency_medicine",
    ),

    # --- Neurological ---
    RedFlagRule(
        id="RF-NEURO-001",
        label="Possible stroke (FAST pattern)",
        urgency=UrgencyLevel.EMERGENCY,
        category="neurological",
        any_of=[{"facial_droop"}, {"arm_weakness"}, {"speech_difficulty"}],
        any_of_min=1,
        rationale=(
            "Facial drooping, one-sided weakness or slurred speech are stroke "
            "warning signs. Stroke treatment is time-critical."
        ),
        required_capabilities=["emergency", "ct_scan", "neurology"],
        specialty_code="neurology",
    ),
    RedFlagRule(
        id="RF-NEURO-002",
        label="Possible meningitis",
        urgency=UrgencyLevel.EMERGENCY,
        category="neurological",
        all_of=[{"fever", "high_fever"}, {"neck_stiffness"}],
        any_of=[{"severe_headache"}, {"headache"}, {"rash"}, {"confusion"}],
        any_of_min=1,
        rationale=(
            "Fever with neck stiffness and headache or rash may indicate "
            "meningitis, which requires immediate treatment."
        ),
        required_capabilities=["emergency", "laboratory"],
        specialty_code="emergency_medicine",
    ),
    RedFlagRule(
        id="RF-NEURO-003",
        label="Thunderclap or worst-ever headache",
        urgency=UrgencyLevel.EMERGENCY,
        category="neurological",
        all_of=[{"severe_headache"}],
        any_of=[{"vomiting"}, {"blurred_vision"}, {"neck_stiffness"}, {"confusion"}],
        any_of_min=1,
        rationale=(
            "A sudden severe headache with vomiting, visual change or neck "
            "stiffness can indicate bleeding around the brain."
        ),
        required_capabilities=["emergency", "ct_scan"],
        specialty_code="neurology",
    ),
    RedFlagRule(
        id="RF-NEURO-004",
        label="Active seizure activity",
        urgency=UrgencyLevel.EMERGENCY,
        category="neurological",
        all_of=[{"seizure"}],
        rationale="A new or ongoing seizure requires emergency assessment.",
        required_capabilities=["emergency", "neurology"],
        specialty_code="neurology",
    ),

    # --- Respiratory ---
    RedFlagRule(
        id="RF-RESP-001",
        label="Severe respiratory distress",
        urgency=UrgencyLevel.EMERGENCY,
        category="respiratory",
        all_of=[{"shortness_of_breath"}],
        any_of=[{"confusion"}, {"loss_of_consciousness"}, {"swelling"}],
        any_of_min=1,
        rationale=(
            "Breathing difficulty with confusion, collapse or facial swelling "
            "suggests a severe or allergic airway problem."
        ),
        required_capabilities=["emergency"],
        specialty_code="emergency_medicine",
    ),
    RedFlagRule(
        id="RF-RESP-002",
        label="Coughing blood",
        urgency=UrgencyLevel.URGENT,
        category="respiratory",
        all_of=[{"coughing_blood"}],
        rationale=(
            "Coughing up blood needs prompt investigation including a chest "
            "X-ray to identify the underlying cause."
        ),
        required_capabilities=["chest_xray", "laboratory"],
        specialty_code="respiratory_medicine",
    ),
    RedFlagRule(
        id="RF-RESP-003",
        label="Fever with breathing difficulty",
        urgency=UrgencyLevel.URGENT,
        category="respiratory",
        all_of=[{"fever", "high_fever"}, {"shortness_of_breath"}],
        rationale=(
            "Fever together with breathlessness may indicate a chest infection "
            "such as pneumonia and should be assessed promptly with imaging."
        ),
        required_capabilities=["chest_xray", "laboratory"],
        specialty_code="respiratory_medicine",
    ),

    # --- Bleeding / GI ---
    RedFlagRule(
        id="RF-BLEED-001",
        label="Severe or uncontrolled bleeding",
        urgency=UrgencyLevel.EMERGENCY,
        category="bleeding",
        all_of=[{"severe_bleeding"}],
        rationale="Heavy bleeding that does not stop requires emergency care.",
        required_capabilities=["emergency", "blood_bank"],
        specialty_code="emergency_medicine",
    ),
    RedFlagRule(
        id="RF-GI-001",
        label="Gastrointestinal bleeding",
        urgency=UrgencyLevel.EMERGENCY,
        category="gastrointestinal",
        any_of=[{"vomiting_blood"}, {"blood_in_stool"}],
        any_of_min=1,
        rationale=(
            "Vomiting blood or passing black/bloody stools indicates bleeding "
            "in the digestive tract and needs immediate assessment."
        ),
        required_capabilities=["emergency", "laboratory"],
        specialty_code="gastroenterology",
    ),
    RedFlagRule(
        id="RF-GI-002",
        label="Severe abdominal pain",
        urgency=UrgencyLevel.URGENT,
        category="gastrointestinal",
        all_of=[{"severe_abdominal_pain"}],
        rationale=(
            "Severe abdominal pain can have surgical causes and should be "
            "assessed without delay."
        ),
        required_capabilities=["emergency", "ultrasound"],
        specialty_code="general_surgery",
    ),

    # --- Maternal ---
    RedFlagRule(
        id="RF-MAT-001",
        label="Bleeding in pregnancy",
        urgency=UrgencyLevel.EMERGENCY,
        category="maternal",
        all_of=[{"vaginal_bleeding"}],
        context_check=_is_pregnant,
        rationale=(
            "Any vaginal bleeding during pregnancy needs immediate assessment "
            "at a facility with maternity services."
        ),
        required_capabilities=["emergency", "obstetrics", "ultrasound"],
        specialty_code="obstetrics_gynaecology",
    ),
    RedFlagRule(
        id="RF-MAT-002",
        label="Possible pre-eclampsia",
        urgency=UrgencyLevel.EMERGENCY,
        category="maternal",
        context_check=_is_pregnant,
        any_of=[{"severe_headache"}, {"blurred_vision"}, {"swelling"}, {"severe_abdominal_pain"}],
        any_of_min=2,
        rationale=(
            "Severe headache, visual changes, swelling or upper abdominal pain "
            "in pregnancy together suggest pre-eclampsia, which can worsen "
            "rapidly for mother and baby."
        ),
        required_capabilities=["emergency", "obstetrics"],
        specialty_code="obstetrics_gynaecology",
    ),
    RedFlagRule(
        id="RF-MAT-003",
        label="Reduced fetal movement",
        urgency=UrgencyLevel.EMERGENCY,
        category="maternal",
        all_of=[{"reduced_fetal_movement"}],
        context_check=_is_pregnant,
        rationale=(
            "A noticeable reduction in baby's movements requires same-day "
            "assessment with fetal monitoring."
        ),
        required_capabilities=["obstetrics", "ultrasound"],
        specialty_code="obstetrics_gynaecology",
    ),
    RedFlagRule(
        id="RF-MAT-004",
        label="Preterm rupture of membranes",
        urgency=UrgencyLevel.EMERGENCY,
        category="maternal",
        all_of=[{"leaking_fluid"}],
        context_check=_is_pregnant,
        rationale=(
            "Fluid leaking during pregnancy may indicate the waters have broken "
            "and needs maternity assessment."
        ),
        required_capabilities=["obstetrics", "emergency"],
        specialty_code="obstetrics_gynaecology",
    ),
    RedFlagRule(
        id="RF-MAT-005",
        label="Postpartum warning signs",
        urgency=UrgencyLevel.EMERGENCY,
        category="maternal",
        context_check=_is_postpartum,
        any_of=[{"severe_bleeding"}, {"high_fever"}, {"severe_headache"}, {"vaginal_bleeding"}],
        any_of_min=1,
        rationale=(
            "Heavy bleeding, fever or severe headache after delivery can "
            "indicate postpartum haemorrhage or infection."
        ),
        required_capabilities=["emergency", "obstetrics"],
        specialty_code="obstetrics_gynaecology",
    ),

    # --- Mental health ---
    RedFlagRule(
        id="RF-MH-001",
        label="Risk of self-harm",
        urgency=UrgencyLevel.EMERGENCY,
        category="mental_health",
        all_of=[{"suicidal_ideation"}],
        rationale=(
            "Thoughts of self-harm or suicide need immediate support from a "
            "mental-health professional or emergency service."
        ),
        required_capabilities=["emergency", "psychiatry"],
        specialty_code="psychiatry",
    ),

    # --- Paediatric ---
    RedFlagRule(
        id="RF-PAED-001",
        label="Unwell infant",
        urgency=UrgencyLevel.EMERGENCY,
        category="paediatric",
        context_check=_is_infant,
        any_of=[{"child_not_feeding"}, {"child_lethargy"}, {"high_fever"}, {"seizure"}],
        any_of_min=1,
        rationale=(
            "Poor feeding, unusual sleepiness or high fever in a very young "
            "child requires urgent paediatric assessment."
        ),
        required_capabilities=["emergency", "paediatrics"],
        specialty_code="paediatrics",
    ),

    # --- Elderly ---
    RedFlagRule(
        id="RF-GER-001",
        label="Fall with injury in an older adult",
        urgency=UrgencyLevel.URGENT,
        category="geriatric",
        context_check=_is_elderly,
        all_of=[{"injury"}],
        rationale=(
            "A fall in an older adult carries a higher risk of fracture or head "
            "injury and should be assessed with imaging."
        ),
        required_capabilities=["xray", "emergency"],
        specialty_code="orthopaedics",
    ),
    RedFlagRule(
        id="RF-GER-002",
        label="New confusion in an older adult",
        urgency=UrgencyLevel.URGENT,
        category="geriatric",
        context_check=_is_elderly,
        all_of=[{"confusion"}],
        rationale=(
            "New confusion in an older adult often has a treatable medical "
            "cause such as infection and needs prompt review."
        ),
        required_capabilities=["laboratory", "emergency"],
        specialty_code="general_medicine",
    ),

    # --- Infection ---
    RedFlagRule(
        id="RF-INF-001",
        label="Persistent high fever",
        urgency=UrgencyLevel.URGENT,
        category="infection",
        all_of=[{"high_fever"}],
        rationale=(
            "A persistently high fever should be investigated to identify the "
            "underlying infection."
        ),
        required_capabilities=["laboratory"],
        specialty_code="general_medicine",
    ),
]


RULES_BY_ID = {rule.id: rule for rule in RULES}
RULE_ENGINE_VERSION = "1.0.0"
