"""Noticing that a doctor's follow-up never happened.

When a doctor completes a consultation they can tick "follow-up required" and
set a date. Those two columns have been written since the clinical module was
built, and **no code has ever read them** except a counter on the doctor's
dashboard. The date passes, nobody books, and nothing anywhere notices.

This is the same shape as the unconverted referral, with one difference that
makes it worth its own detector: a named doctor asked for it. So the follow-up
should go back to *that* doctor rather than to the matcher — continuity of care
is the point of a follow-up, and sending the patient to whoever has the
earliest slot defeats it.

Two audiences, in order:

1. **The patient**, with a bookable appointment with the same doctor.
2. **The doctor**, once, if it is still unbooked a week later — a batch across
   all their lapsed follow-ups rather than one proposal per patient, because a
   clinician with eleven approve buttons approves none of them.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import actions
from app.core.db import SessionLocal
from app.models.agentic import AgentTask
from app.models.care import Appointment, Consultation
from app.models.enums import AppointmentStatus, VisitType
from app.models.identity import User
from app.models.providers import Doctor
from app.services import clock
from app.services.availability import next_available_slot
from app.services.jobs import register, runner

logger = logging.getLogger(__name__)

TASK_KIND = "followup_lapsed"

# Days past the requested follow-up date before it counts as lapsed. A patient
# booking three days late has not been forgotten about.
GRACE_DAYS = 3

# When the doctor is told, if the patient still has not booked.
DOCTOR_ESCALATION_DAYS = 10

# The doctor's batch is capped for the same reason the patient's inbox is.
MAX_RECALL_BATCH = 12


def _booked_since(db: Session, consultation: Consultation) -> bool:
    """Has the patient seen this doctor again since the consultation?"""
    return db.execute(
        select(Appointment.id)
        .where(
            Appointment.patient_user_id == consultation.patient_user_id,
            Appointment.doctor_id == consultation.doctor_id,
            Appointment.created_at > consultation.created_at,
            Appointment.status != AppointmentStatus.CANCELLED,
        )
        .limit(1)
    ).scalar_one_or_none() is not None


def detect(db: Session) -> int:
    """Find follow-ups a doctor asked for that nobody arranged."""
    today = clock.today()
    cutoff = today - timedelta(days=GRACE_DAYS)

    lapsed = db.execute(
        select(Consultation).where(
            Consultation.follow_up_required.is_(True),
            Consultation.follow_up_date.isnot(None),
            Consultation.follow_up_date < cutoff,
        )
    ).scalars().all()

    queued = 0
    for consultation in lapsed:
        if _booked_since(db, consultation):
            continue

        overdue_days = (today - consultation.follow_up_date).days
        stage = "recall" if overdue_days >= DOCTOR_ESCALATION_DAYS else "book"

        if runner.enqueue(
            db,
            kind=TASK_KIND,
            dedupe_key=f"followup:{consultation.id}:{stage}",
            subject_user_id=consultation.patient_user_id,
            payload={
                "consultation_id": consultation.id,
                "stage": stage,
                "overdue_days": overdue_days,
            },
        ):
            queued += 1

    return queued


@runner.handler(TASK_KIND)
def handle(db: Session, task: AgentTask) -> None:
    consultation = db.get(Consultation, task.payload.get("consultation_id"))
    if consultation is None or not consultation.follow_up_required:
        return
    if _booked_since(db, consultation):
        return  # arranged between detection and handling

    patient = db.get(User, consultation.patient_user_id)
    doctor = db.get(Doctor, consultation.doctor_id)
    if patient is None or doctor is None:
        return

    if task.payload.get("stage") == "recall":
        _propose_recall(db, doctor, patient, consultation, task)
    else:
        _propose_booking(db, doctor, patient, consultation, task)


def _propose_booking(db, doctor, patient, consultation, task) -> None:
    """Offer the patient the follow-up their doctor asked for."""
    slot = next_available_slot(db, doctor, visit_type=VisitType.PHYSICAL)
    if slot is None:
        logger.info("No slot with doctor %s for a follow-up", doctor.id)
        return

    when = slot.start.astimezone(clock.local_zone()).strftime("%a %d %b, %I:%M %p")
    overdue = int(task.payload.get("overdue_days", 0))
    notes = (consultation.follow_up_notes or "").strip()

    actions.propose(
        db,
        subject=patient,
        action_name="book_appointment",
        args={
            "doctor_id": doctor.id,
            "scheduled_start": slot.start.isoformat(),
            "visit_type": str(VisitType.PHYSICAL),
            "reason": "Follow-up requested by your doctor",
        },
        title=f"Book your follow-up with {doctor.user.full_name}?",
        preview_text=(
            f"{doctor.user.full_name} asked to see you again "
            f"{overdue} day(s) ago and this has not been booked.\n\n"
            f"{when} · Rs {doctor.consultation_fee_lkr:,.0f}"
            + (f"\n\nTheir note: {notes}" if notes else "")
        ),
        evidence={
            "detector_id": "followup_lapsed",
            "consultation_id": consultation.id,
            "requested_by_doctor_id": doctor.id,
            "overdue_days": overdue,
        },
        idempotency_key=f"followup_book:{consultation.id}",
        origin="job",
        origin_ref=consultation.id,
    )
    db.commit()


def _propose_recall(db, doctor, patient, consultation, task) -> None:
    """Tell the doctor, once, batched across their lapsed follow-ups.

    Addressed to the doctor rather than about the patient, so it appears in
    the clinician's own queue. Consent is not inherited from the patient: the
    approval endpoint re-derives it from the treating relationship.
    """
    from app.models.agentic import ActionProposal

    doctor_user = doctor.user
    if doctor_user is None:
        return

    # Fold into today's batch if one is already pending, rather than adding a
    # second card for the same clinician.
    key = f"followup_recall:{doctor.id}:{clock.today().isoformat()}"
    existing = db.execute(
        select(ActionProposal).where(
            ActionProposal.idempotency_key == key,
            ActionProposal.status == "pending",
        )
    ).scalar_one_or_none()

    if existing is not None:
        ids = list(existing.args.get("consultation_ids") or [])
        if consultation.id not in ids and len(ids) < MAX_RECALL_BATCH:
            ids.append(consultation.id)
            existing.args = {**existing.args, "consultation_ids": ids}
            existing.title = f"{len(ids)} follow-ups have lapsed — send a recall?"
            db.commit()
        return

    actions.propose(
        db,
        subject=patient,
        audience=doctor_user,
        action_name="send_followup_recall",
        args={"consultation_ids": [consultation.id]},
        title="1 follow-up has lapsed — send a recall?",
        preview_text=(
            "A follow-up you asked for has not been booked, more than "
            f"{DOCTOR_ESCALATION_DAYS} days past the date you set. "
            "Approving sends the patient a reminder to book with you.\n\n"
            "The reminder names no clinical detail."
        ),
        evidence={
            "detector_id": "followup_lapsed",
            "stage": "recall",
            "requested_by_doctor_id": doctor.id,
        },
        idempotency_key=key,
        origin="job",
        origin_ref=doctor.id,
        ttl_days=7,
    )
    db.commit()


@register(
    "detect_lapsed_followups",
    seconds=12 * 60 * 60,
    description="Find follow-ups a doctor asked for that were never booked",
)
def job() -> None:
    db = SessionLocal()
    try:
        detect(db)
    finally:
        db.close()
