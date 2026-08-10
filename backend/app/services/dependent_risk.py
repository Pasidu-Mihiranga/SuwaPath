"""Ranking a guardian's dependents by who needs attention first.

A guardian looking after three people currently sees three cards in whatever
order the database returned, each with a short status derived from a
three-branch heuristic. Working out who to worry about is left to them, and
the cards give them no help: "Pregnancy week 23" and "No check-in for 2 days"
are not comparable at a glance.

This scores each dependent on the signals the system already has, so the list
can be ordered and the reason stated.

**Every component is gated by the consent scope that covers it**, and that is
not a detail. A guardian who was granted appointments but not medications must
not see a dependent rise to the top of the list because of a medication
problem — the ranking would then leak the existence of something the patient
chose not to share. So an ungranted signal contributes nothing and is named
nowhere, and the score is computed from what this particular guardian is
entitled to see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agentic import ActionProposal
from app.models.care import DailyCheckIn, ElderlyRecord, Medication
from app.models.enums import GuardianPermissionType
from app.models.platform import GuardianAlert
from app.services import clock


@dataclass
class RiskAssessment:
    score: float = 0.0
    reasons: list[tuple[float, str]] = field(default_factory=list)

    # No single signal may dominate the score.
    #
    # The first version weighted each unread alert at 4.0 and a dependent with
    # forty of them scored 164, which made every other signal invisible and
    # the ranking meaningless — the guardian with the noisiest dependent would
    # simply always see that dependent first. A capped contribution keeps
    # "several unread alerts" strong without letting volume swamp a missed
    # medication run or a warning sign.
    COMPONENT_CAP = 5.0

    def add(self, weight: float, reason: str) -> None:
        if weight > 0:
            capped = min(weight, self.COMPONENT_CAP)
            self.score += capped
            self.reasons.append((capped, reason))

    @property
    def headline(self) -> str | None:
        """The single largest contributor, which is what the card shows."""
        if not self.reasons:
            return None
        return max(self.reasons, key=lambda r: r[0])[1]

    @property
    def tone(self) -> str:
        if self.score >= 6:
            return "urgent"
        if self.score >= 3:
            return "attention"
        return "normal"


def _granted(scopes: set[str], permission: GuardianPermissionType) -> bool:
    return (
        str(permission) in scopes
        or str(GuardianPermissionType.FULL_MEDICAL) in scopes
    )


def assess(
    db: Session, *, guardian_user_id: str, patient_user_id: str, scopes: set[str]
) -> RiskAssessment:
    """Score one dependent, using only signals this guardian may see."""
    assessment = RiskAssessment()
    today = clock.today()

    # Unacknowledged alerts. Always visible — an alert was already sent to this
    # guardian under a scope check, so surfacing its existence reveals nothing
    # new.
    critical = db.execute(
        select(GuardianAlert.severity).where(
            GuardianAlert.guardian_user_id == guardian_user_id,
            GuardianAlert.patient_user_id == patient_user_id,
            GuardianAlert.is_acknowledged.is_(False),
        )
    ).scalars().all()
    if critical:
        urgent = sum(1 for s in critical if str(s) == "critical")
        assessment.add(
            min(5.0, 2.5 * urgent + 0.5 * (len(critical) - urgent)),
            f"{len(critical)} unread alert(s)"
            + (f", {urgent} critical" if urgent else ""),
        )

    if _granted(scopes, GuardianPermissionType.WELLBEING):
        record = db.execute(
            select(ElderlyRecord).where(
                ElderlyRecord.patient_user_id == patient_user_id
            )
        ).scalar_one_or_none()
        if record and record.consecutive_missed_checkins:
            days = record.consecutive_missed_checkins
            assessment.add(min(4.0, days * 1.2), f"No check-in for {days} day(s)")

        recent = db.execute(
            select(DailyCheckIn)
            .where(DailyCheckIn.patient_user_id == patient_user_id)
            .order_by(DailyCheckIn.check_in_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if recent and recent.triggered_alert:
            assessment.add(5.0, "Warning sign reported at check-in")

    if _granted(scopes, GuardianPermissionType.MEDICATIONS):
        from app.services.detectors.medication import consecutive_missed

        medications = db.execute(
            select(Medication).where(
                Medication.patient_user_id == patient_user_id,
                Medication.is_active.is_(True),
            )
        ).scalars().all()
        worst, worst_name = 0, ""
        for medication in medications:
            run = consecutive_missed(db, medication)
            if run > worst:
                worst, worst_name = run, medication.name
        if worst >= 2:
            weight = min(5.0, worst * 1.0)
            assessment.add(weight, f"{worst} missed doses of {worst_name}")

    if _granted(scopes, GuardianPermissionType.APPOINTMENTS):
        pending = db.execute(
            select(ActionProposal.id).where(
                ActionProposal.subject_user_id == patient_user_id,
                ActionProposal.status == "pending",
                ActionProposal.audience_user_id.is_(None),
            )
        ).scalars().all()
        if pending:
            assessment.add(
                min(3.0, 1.0 * len(pending)),
                f"{len(pending)} care suggestion(s) awaiting a decision",
            )

    # Recency of *any* contact. A dependent nobody has heard from is the case
    # no single detector can state, because every one of them watches a single
    # source and silence looks the same as nothing being wrong.
    if _granted(scopes, GuardianPermissionType.WELLBEING):
        last_seen = db.execute(
            select(DailyCheckIn.check_in_date)
            .where(DailyCheckIn.patient_user_id == patient_user_id)
            .order_by(DailyCheckIn.check_in_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last_seen and (today - last_seen) > timedelta(days=14):
            assessment.add(
                3.0, f"No contact in {(today - last_seen).days} days"
            )

    return assessment
