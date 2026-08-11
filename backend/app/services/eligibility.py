"""Who a care programme is for, and what happens when someone else asks.

Until now enrolment had no criteria whatsoever: any patient could join any
programme, so a 25-year-old man could enrol in Elderly Care and someone who
has never been pregnant could enrol in Maternal & Postpartum Care. The
programme dashboards would then render a pregnancy week for a patient with no
pregnancy, which is not a cosmetic problem — a maternal danger-sign check-in
asks about reduced fetal movement.

The design question that raises is whether to hard-block. Enterprise care
management does not: eligibility is computed from the record, ineligible
patients are shown *as* ineligible with the reason, and a clinician can
override with a recorded justification. Hard blocks get worked around by
putting false data in the record, which is worse than an audited override —
a 62-year-old with genuine frailty belongs in elderly care, and the system
should not force someone to lie about their age to get there.

So the rules here return one of three answers:

**eligible**    the record supports it.
**confirm**     plausible but unverified — the patient is asked to confirm
                something (a due date, that the programme suits them) and the
                acknowledgement is recorded on the enrolment.
**ineligible**  contradicted by the record in a way no confirmation fixes.

Only the last one refuses, and it is used sparingly: today for a maternal
programme where the profile records a sex that cannot be pregnant. That is a
factual contradiction, not a judgement about the person, and the message says
which field to change if the record is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.care import CareProgramme, MaternalRecord
from app.models.enums import ProgrammeType
from app.models.identity import PatientProfile

# The age elderly care is designed around. Not a cliff: below it the answer is
# "confirm", not "no".
ELDERLY_AGE = 65


@dataclass(frozen=True)
class Eligibility:
    verdict: str  # eligible | confirm | ineligible
    reason: str = ""
    # What the patient is being asked to agree to, when verdict == confirm.
    confirmation: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict in ("eligible", "confirm")

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "confirmation": self.confirmation,
            "allowed": self.allowed,
        }


ELIGIBLE = Eligibility("eligible")


def assess(
    programme: CareProgramme,
    profile: PatientProfile | None,
    *,
    maternal: MaternalRecord | None = None,
) -> Eligibility:
    """Can this patient join this programme, and on what terms?"""
    programme_type = str(programme.programme_type)
    age = getattr(profile, "age", None)
    sex = str(getattr(profile, "sex", "") or "").lower()
    pregnant = bool(getattr(profile, "is_pregnant", False))

    if programme_type in (str(ProgrammeType.MATERNAL), str(ProgrammeType.POSTPARTUM)):
        # The one factual contradiction worth refusing on. Phrased as a
        # statement about the record rather than about the person, with the
        # field to correct if the record is wrong.
        if sex == "male":
            return Eligibility(
                "ineligible",
                "This profile records sex as male, so this programme cannot "
                "be started from it.",
                "",
            )
        if programme_type == str(ProgrammeType.POSTPARTUM):
            had_pregnancy = bool(maternal and maternal.expected_delivery_date)
            if not (had_pregnancy or (maternal and maternal.is_postpartum)):
                return Eligibility(
                    "confirm",
                    "Postpartum care follows a recent birth.",
                    "Confirm you have given birth in the last twelve weeks.",
                )
            return ELIGIBLE
        if not pregnant:
            return Eligibility(
                "confirm",
                "This profile does not record a current pregnancy.",
                "Confirm you are pregnant, and add your expected delivery "
                "date so check-ins are timed correctly.",
            )
        return ELIGIBLE

    if programme_type == str(ProgrammeType.ELDERLY):
        if age is not None and age < ELDERLY_AGE:
            return Eligibility(
                "confirm",
                f"Elderly care is designed for people aged {ELDERLY_AGE} and over.",
                "Confirm this programme suits your needs — for example if you "
                "live with frailty, reduced mobility or several long-term "
                "conditions.",
            )
        return ELIGIBLE

    if programme_type == str(ProgrammeType.SEXUAL_HEALTH):
        # Never gated. This pathway exists precisely for people who would not
        # ask, and an eligibility question is one more reason not to.
        return ELIGIBLE

    return ELIGIBLE
