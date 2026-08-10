"""Closing appointments that time has already decided.

Every one of the eight appointment states is entered by a human pressing
something. Nothing is driven by the clock, so an appointment whose slot came
and went stays `confirmed` for ever unless a clinician remembers to mark it.

That has consequences beyond tidiness. The no-show model
(`services/analytics.py`) trains on `COMPLETED` versus `NO_SHOW` over the last
90 days, so in a live system it would learn from seeded history and nothing
else — the label it needs is the one nobody produces. And a patient who missed
an appointment is exactly the person most worth offering another.

This sweep supplies that label, and turns a missed appointment into an offer
rather than a dead row.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import SessionLocal
from app.models.agentic import AgentTask
from app.models.care import Appointment
from app.models.enums import (
    AppointmentStatus,
    NotificationCategory,
    NotificationPriority,
)
from app.models.platform import Notification
from app.services import clock
from app.services.jobs import register, runner

logger = logging.getLogger(__name__)

TASK_KIND = "appointment_missed"

# How long after the end of a slot before absence is a fact rather than
# lateness. Generous on purpose: a patient stuck in Colombo traffic who is
# seen twenty minutes late did attend, and recording otherwise would both
# insult them and poison the training label.
GRACE_MINUTES = 45

# A consultation left open this long was forgotten by the clinic, not still
# running. Surfaced to the hospital rather than closed automatically, because
# what happened in it is a clinical record nobody here can reconstruct.
STALE_CONSULTATION_HOURS = 8


def detect(db: Session) -> int:
    """Mark elapsed appointments as no-shows and queue a follow-up for each."""
    now = clock.now()
    cutoff = now - timedelta(minutes=GRACE_MINUTES)

    elapsed = db.execute(
        select(Appointment)
        .options(selectinload(Appointment.doctor))
        .where(
            Appointment.status.in_(
                (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)
            ),
            Appointment.scheduled_end < cutoff,
        )
        .limit(200)
    ).scalars().all()

    # The sweep writes the model directly rather than going through the
    # endpoint, so it must consult the same transition table the endpoint uses
    # — otherwise there are two notions of a legal state change and only one
    # of them is enforced. `PENDING → NO_SHOW` is legitimately absent from it:
    # an appointment nobody ever confirmed should be cancelled, not recorded
    # as a patient failing to attend.
    from app.api.v1.appointments import ALLOWED_TRANSITIONS

    queued = 0
    for appointment in elapsed:
        current = str(appointment.status)
        if str(AppointmentStatus.NO_SHOW) not in ALLOWED_TRANSITIONS.get(current, set()):
            appointment.status = AppointmentStatus.CANCELLED
            appointment.cancelled_at = now
            appointment.cancellation_reason = "Never confirmed; slot elapsed."
            continue

        # The status change is a fact about the past and is applied directly.
        # Only the *response* to it goes through the task queue.
        #
        # Marked as swept rather than observed. Nobody watched this patient
        # fail to arrive — the appointment simply elapsed with no one closing
        # it, and the no-show model must not treat that as a label.
        appointment.status = AppointmentStatus.NO_SHOW
        appointment.status_source = "auto_sweep"

        if runner.enqueue(
            db,
            kind=TASK_KIND,
            dedupe_key=f"noshow_sweep:{appointment.id}",
            subject_user_id=appointment.patient_user_id,
            payload={"appointment_id": appointment.id},
        ):
            queued += 1

    if elapsed:
        db.commit()
        logger.info("swept %d elapsed appointment(s)", len(elapsed))

    _flag_stale_consultations(db, now)
    return queued


def _flag_stale_consultations(db: Session, now) -> None:
    """Tell the hospital about consultations nobody closed.

    Writes `HospitalAlert`, a table that has been read by the operations
    dashboard since the beginning and written by nothing.
    """
    from app.models.enums import AlertSeverity
    from app.models.platform import HospitalAlert

    stuck = db.execute(
        select(Appointment).where(
            Appointment.status.in_(
                (AppointmentStatus.CHECKED_IN, AppointmentStatus.IN_CONSULTATION)
            ),
            Appointment.scheduled_end < now - timedelta(hours=STALE_CONSULTATION_HOURS),
            Appointment.hospital_id.isnot(None),
        )
        .limit(50)
    ).scalars().all()

    for appointment in stuck:
        exists = db.execute(
            select(HospitalAlert.id).where(
                HospitalAlert.hospital_id == appointment.hospital_id,
                HospitalAlert.alert_type == "stale_consultation",
                HospitalAlert.is_resolved.is_(False),
                HospitalAlert.meta["appointment_id"].astext == appointment.id,
            )
        ).scalar_one_or_none()
        if exists:
            continue

        db.add(
            HospitalAlert(
                hospital_id=appointment.hospital_id,
                alert_type="stale_consultation",
                severity=AlertSeverity.ATTENTION,
                title="Consultation left open",
                detail=(
                    "An appointment has been in progress for more than "
                    f"{STALE_CONSULTATION_HOURS} hours and has not been closed."
                ),
                meta={"appointment_id": appointment.id},
            )
        )
    if stuck:
        db.commit()


@runner.handler(TASK_KIND)
def handle(db: Session, task: AgentTask) -> None:
    """Offer the patient another appointment.

    Deliberately a notification rather than a booking proposal: a missed
    appointment may have been missed because the person changed their mind,
    got better, or went elsewhere. Proposing a specific new slot assumes they
    still want it. The reminder points them back at the recommendation, and if
    that recommendation really is still unconverted the referral detector will
    prepare a concrete booking on its own schedule.
    """
    appointment = db.get(Appointment, task.payload.get("appointment_id"))
    if appointment is None:
        return

    db.add(
        Notification(
            user_id=appointment.patient_user_id,
            category=NotificationCategory.APPOINTMENT,
            priority=NotificationPriority.HIGH,
            title="You missed your appointment",
            body=(
                "Your appointment has been marked as missed. If you still need "
                "to be seen, you can book another time."
            ),
            action_type="appointment",
            action_id=appointment.id,
        )
    )
    db.commit()


@register(
    "sweep_elapsed_appointments",
    seconds=15 * 60,
    description="Mark elapsed appointments as no-shows and offer a rebook",
)
def job() -> None:
    db = SessionLocal()
    try:
        detect(db)
    finally:
        db.close()
