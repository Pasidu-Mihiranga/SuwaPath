"""Noticing that recommended care never happened.

This is the detector the whole autonomy layer exists for.

The product is good at the moment of advice: it takes a history, decides
urgency deterministically, and names a specialty and the facility capabilities
the patient will need. Then the conversation ends, and whether anything comes
of it depends entirely on the patient remembering, finding time, and working
out where to go. Nobody sends a message saying "I never booked that" — that is
precisely why unconverted referrals are invisible.

So this runs on a schedule, compares intent against what actually happened, and
where a gap has persisted long enough to be real rather than recent, prepares
the booking the patient would otherwise have to construct themselves: a named
doctor at a facility that can perform the required test, a real slot, a real
fee, and the reason it was chosen.

Two sources of intent
---------------------
`Recommendation` — produced by the assistant or by a report or image analysis.
Conversion is checked through `Appointment.recommendation_id`, a foreign key
that has existed since the beginning and, until now, was written but never
read.

`Referral` — written by a doctor after a consultation. Not yet covered here:
recommendations already carry the capability requirements that make a proposal
specific, and a referral does not, so it needs its own matching path rather
than a shared one. Listed in the log as the next detector.

Escalation, not repetition
--------------------------
A nudge that arrives every day is noise, and noise about health is worse than
silence because it teaches people to ignore the channel. Each stage of the
ladder fires at most once, enforced by the dedupe key rather than by
remembering to check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import actions
from app.core.db import SessionLocal
from app.models.agentic import AgentTask
from app.models.care import Appointment
from app.models.clinical import Recommendation
from app.models.enums import AppointmentStatus, UrgencyLevel, VisitType
from app.models.identity import User
from app.models.providers import Doctor
from app.services import clock
from app.services.availability import next_available_slot
from app.services.jobs import register
from app.services.jobs import runner
from app.services.matching import MatchCriteria, match_doctors

logger = logging.getLogger(__name__)

TASK_KIND = "referral_followup"


@dataclass(frozen=True)
class Stage:
    """One rung of the escalation ladder."""

    name: str
    after_days: float


# Urgent care that has not been arranged is a different problem from a routine
# check nobody got round to, and the pacing reflects that rather than applying
# one interval to both.
LADDER: dict[str, list[Stage]] = {
    "urgent": [Stage("nudge", 2), Stage("guardian", 5), Stage("navigator", 7)],
    "routine": [Stage("nudge", 7), Stage("second_nudge", 14), Stage("close", 30)],
}


def _ladder_for(urgency: str) -> list[Stage]:
    return LADDER["urgent"] if urgency in {"emergency", "urgent"} else LADDER["routine"]


def _due_stage(age_days: float, urgency: str) -> Stage | None:
    """The furthest rung this gap has reached, or None if it is still fresh."""
    reached = [s for s in _ladder_for(urgency) if age_days >= s.after_days]
    return reached[-1] if reached else None


# --------------------------------------------------------------------------
# Detection — reads only, enqueues only
# --------------------------------------------------------------------------
def _converted_recommendation(db: Session, rec: Recommendation) -> bool:
    """Did an appointment come out of this recommendation?

    Counted either by the explicit link or by the patient booking the right
    specialty afterwards — someone who found the right clinic on their own has
    converted, and chasing them would be both wrong and irritating.
    """
    linked = db.execute(
        select(Appointment.id)
        .where(
            Appointment.recommendation_id == rec.id,
            Appointment.status != AppointmentStatus.CANCELLED,
        )
        .limit(1)
    ).scalar_one_or_none()
    if linked:
        return True

    if not rec.specialty_id:
        return False

    same_specialty = db.execute(
        select(Appointment.id)
        .join(Doctor, Doctor.id == Appointment.doctor_id)
        .where(
            Appointment.patient_user_id == rec.patient_user_id,
            Appointment.created_at >= rec.created_at,
            Appointment.status != AppointmentStatus.CANCELLED,
            Doctor.specialty_id == rec.specialty_id,
        )
        .limit(1)
    ).scalar_one_or_none()
    return same_specialty is not None


# How many open follow-ups one person may have at once.
#
# Without this the detector is technically correct and practically useless: a
# patient with a long history has dozens of active recommendations, and
# chasing all of them at once produces an inbox nobody reads. An unread
# reminder is worth less than no reminder, because it also teaches the person
# to ignore the next one. Three is a number a person can act on.
MAX_OPEN_PER_PATIENT = 3

# Urgency first, then the longest-waiting, so the cap spends its budget on
# what matters rather than on whatever the database returned first.
_URGENCY_RANK = {"emergency": 0, "urgent": 1, "routine": 2, "self_care": 3}


def _open_followups(db: Session, patient_user_id: str) -> int:
    """Follow-ups already competing for this person's attention."""
    from app.models.agentic import ActionProposal

    pending_tasks = (
        db.query(AgentTask)
        .filter(
            AgentTask.subject_user_id == patient_user_id,
            AgentTask.kind == TASK_KIND,
            AgentTask.status.in_(("queued", "running")),
        )
        .count()
    )
    pending_proposals = (
        db.query(ActionProposal)
        .filter(
            ActionProposal.subject_user_id == patient_user_id,
            ActionProposal.status == "pending",
        )
        .count()
    )
    return pending_tasks + pending_proposals


def detect(db: Session) -> int:
    """Find stalled care journeys and queue a follow-up for the most pressing.

    Pure: nothing here notifies, books or alerts. It decides only *that*
    something needs attention and lets the handler decide what to do about it.
    """
    now = clock.now()
    queued = 0

    recommendations = db.execute(
        select(Recommendation).where(
            Recommendation.is_active.is_(True),
            Recommendation.patient_user_id.isnot(None),
        )
    ).scalars().all()

    # Most urgent first, then oldest, so the per-patient cap is spent on the
    # care that matters most rather than on arbitrary rows.
    recommendations.sort(
        key=lambda r: (_URGENCY_RANK.get(str(r.urgency), 9), r.created_at)
    )

    budget: dict[str, int] = {}

    for rec in recommendations:
        age_days = (now - rec.created_at).total_seconds() / 86400
        urgency = str(rec.urgency)
        stage = _due_stage(age_days, urgency)
        if stage is None:
            continue

        patient_id = rec.patient_user_id
        if patient_id not in budget:
            budget[patient_id] = MAX_OPEN_PER_PATIENT - _open_followups(db, patient_id)
        if budget[patient_id] <= 0:
            continue

        if _converted_recommendation(db, rec):
            continue

        if runner.enqueue(
            db,
            kind=TASK_KIND,
            # The stage is part of the identity, so the ladder advances exactly
            # once per rung however often this runs.
            dedupe_key=f"referral_unconverted:{rec.id}:{stage.name}",
            subject_user_id=rec.patient_user_id,
            payload={
                "recommendation_id": rec.id,
                "stage": stage.name,
                "urgency": urgency,
                "age_days": round(age_days, 1),
            },
            priority=10 if urgency in {"emergency", "urgent"} else 0,
        ):
            queued += 1
            budget[patient_id] -= 1

    logger.info("referral detector queued %d follow-up(s)", queued)
    return queued


# --------------------------------------------------------------------------
# Handling — the part that acts
# --------------------------------------------------------------------------
def _propose_booking(db: Session, patient: User, rec: Recommendation, stage: str) -> bool:
    """Prepare the booking this patient was told to make.

    The value is entirely in specificity. "See a gastroenterologist" is what
    the patient already knows and did not act on. A named doctor, at a facility
    that can run the test the recommendation requires, at a slot that exists,
    for a stated fee, is a different proposition — and it is exactly what the
    existing matcher already computes.
    """
    profile = getattr(patient, "patient_profile", None)
    criteria = MatchCriteria(
        specialty_code=rec.specialty_code,
        secondary_specialty_codes=list(rec.secondary_specialty_codes or []),
        required_capabilities=list(rec.required_capabilities or []),
        urgency=UrgencyLevel(str(rec.urgency)),
        patient_lat=getattr(profile, "latitude", None),
        patient_lon=getattr(profile, "longitude", None),
    )
    matches = match_doctors(db, criteria, limit=3)
    if not matches:
        logger.info("no provider matched recommendation %s", rec.id)
        return False

    best = matches[0]
    slot = best.next_slot or next_available_slot(db, best.doctor)
    if slot is None:
        return False

    when = slot.start.astimezone(clock.local_zone()).strftime("%a %d %b, %I:%M %p")
    capability_note = (
        f" This facility can perform {', '.join(best.matched_capabilities)}."
        if best.matched_capabilities
        else ""
    )
    preview = (
        f"{best.doctor.user.full_name} — {best.doctor.hospital.name}\n"
        f"{when} · Rs {best.doctor.consultation_fee_lkr:,.0f}\n\n"
        f"{best.explanation}{capability_note}"
    )

    proposal_id = actions.propose(
        db,
        subject=patient,
        action_name="book_appointment",
        args={
            "doctor_id": best.doctor.id,
            "scheduled_start": slot.start.isoformat(),
            "visit_type": str(VisitType.PHYSICAL),
            "reason": rec.care_category,
            "recommendation_id": rec.id,
        },
        title=f"Book your {rec.care_category} appointment?",
        preview_text=preview,
        evidence={
            "detector_id": "referral_conversion",
            "recommendation_id": rec.id,
            "stage": stage,
            "urgency": str(rec.urgency),
        },
        idempotency_key=f"book:{rec.id}:{stage}",
        origin="job",
        origin_ref=rec.id,
    )
    return proposal_id is not None


@runner.handler(TASK_KIND)
def handle(db: Session, task: AgentTask) -> None:
    """Act on one stalled care journey."""
    rec = db.get(Recommendation, task.payload.get("recommendation_id"))
    if rec is None or not rec.is_active:
        return  # resolved or withdrawn since detection; nothing to do

    patient = db.get(User, task.subject_user_id)
    if patient is None:
        return

    stage = task.payload.get("stage", "nudge")

    if stage in {"nudge", "second_nudge"}:
        _propose_booking(db, patient, rec, stage)

    elif stage == "guardian":
        from app.models.enums import AlertSeverity, GuardianPermissionType

        actions.get("raise_guardian_alert").run(
            db,
            patient=patient,
            alert_type="care_not_arranged",
            severity=AlertSeverity.ATTENTION,
            title="Recommended care has not been arranged",
            detail=(
                f"{patient.full_name} was advised to see a "
                f"{rec.specialty_code.replace('_', ' ')} "
                f"{int(task.payload.get('age_days', 0))} days ago and no "
                "appointment has been booked."
            ),
            permission=GuardianPermissionType.APPOINTMENTS,
            evidence={"detector_id": "referral_conversion", "recommendation_id": rec.id},
        )
        db.commit()

    elif stage == "navigator":
        # Two nudges and a guardian alert have not produced a booking, so the
        # automated approach has run out of road.
        #
        # The obvious move — page a facility that could provide the care — is
        # not taken deliberately. It would disclose that this person needs
        # urgent care to a hospital they never chose and have no relationship
        # with, which is a real PHI disclosure invented to satisfy a workflow.
        # There is no navigator role in this system to hand to, so the honest
        # action is to make the reminder unmissable to the people already
        # entitled to see it, and stop.
        from app.models.enums import NotificationCategory, NotificationPriority
        from app.services.delivery import Message, deliver

        # Routed through the delivery layer rather than written straight to
        # the notifications table: this is the one stage where the patient not
        # opening the app is the whole problem, so it earns a second channel.
        # The SMS carries no clinical detail — see services/delivery/sms.py.
        deliver(
            db,
            patient,
            Message(
                title="Please arrange your appointment",
                body=(
                    f"You were advised to see a "
                    f"{rec.specialty_code.replace('_', ' ')} specialist "
                    f"{int(task.payload.get('age_days', 0))} days ago. "
                    "If something is making this difficult, call SuwaPath Care "
                    "on 0112 123 456."
                ),
                priority=str(NotificationPriority.CRITICAL),
                category=str(NotificationCategory.FOLLOW_UP),
                action_type="recommendation",
                action_id=rec.id,
            ),
        )

    elif stage == "close":
        # A routine suggestion nobody acted on in a month is not an emergency;
        # it is a suggestion that has expired. Letting it go quietly is part of
        # not becoming noise.
        rec.is_active = False
        db.commit()


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------
@register(
    "detect_unconverted_referrals",
    seconds=6 * 60 * 60,
    description="Find recommended care that was never booked",
)
def job() -> None:
    db = SessionLocal()
    try:
        detect(db)
    finally:
        db.close()
