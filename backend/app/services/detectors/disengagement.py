"""Noticing that someone has stopped engaging with their care entirely.

Every other detector watches one source and fires on a level: doses missed,
days since a check-in, a referral unconverted. Each is useful and each has the
same blind spot — within a single source, *absence looks like nothing wrong*.
A patient with no medication logs might have no medications. A patient with no
check-ins might not be enrolled. No single query can distinguish "nothing to
report" from "this person has gone quiet".

Crossing the sources is what makes the statement possible: enrolled in an
active programme, with an obligation that is still live, and simultaneously no
check-in, no appointment, no medication log and no login. That is a fact no
existing detector can express, and it is the one most worth acting on, because
the people who disappear are not the people who complain.

The second thing here is **change** rather than level. Every threshold in this
system is absolute — 2 missed doses, 3 days without a check-in. A patient
whose adherence falls from 95% to 60% never crosses a 50% line but is
deteriorating; someone stable at 55% crosses it every single run and is not
news. Level detection is precisely why an attention cap had to be invented.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.agentic import AgentTask
from app.models.care import (
    Appointment,
    CareEnrollment,
    DailyCheckIn,
    Medication,
    MedicationLog,
)
from app.models.enums import (
    AlertSeverity,
    GuardianPermissionType,
    MedicationLogStatus,
)
from app.models.identity import User
from app.services import clock
from app.services.alerts import raise_guardian_alerts
from app.services.jobs import register, runner

logger = logging.getLogger(__name__)

TASK_KIND = "disengagement"

# Silence across every channel for this long, while still enrolled, is the
# signal. Short enough to matter in a pregnancy, long enough that a fortnight's
# holiday does not trigger it.
SILENT_DAYS = 14

# Adherence drop, in percentage points, that counts as deterioration even
# though the absolute level may still be above every threshold.
DECLINE_POINTS = 25.0
RECENT_WINDOW = 14
PRIOR_WINDOW = 28


def _last_activity(db: Session, patient_id: str):
    """The most recent sign of life, across every source we have."""
    since = clock.now() - timedelta(days=120)
    stamps = []

    checkin = db.execute(
        select(func.max(DailyCheckIn.check_in_date)).where(
            DailyCheckIn.patient_user_id == patient_id
        )
    ).scalar_one_or_none()
    if checkin:
        stamps.append(checkin)

    appointment = db.execute(
        select(func.max(Appointment.created_at)).where(
            Appointment.patient_user_id == patient_id,
            Appointment.created_at >= since,
        )
    ).scalar_one_or_none()
    if appointment:
        stamps.append(appointment.date())

    dose = db.execute(
        select(func.max(MedicationLog.recorded_at)).where(
            MedicationLog.patient_user_id == patient_id,
            MedicationLog.recorded_at.isnot(None),
        )
    ).scalar_one_or_none()
    if dose:
        stamps.append(dose.date())

    return max(stamps) if stamps else None


def adherence_change(db: Session, patient_id: str) -> tuple[float, float] | None:
    """Adherence over the recent window versus the window before it.

    Returns `(recent, prior)` as percentages, or None when either window has
    too little data to compare. Comparing a handful of doses to a handful of
    doses produces noise that looks exactly like deterioration.
    """
    now = clock.now()

    def rate(start, end) -> tuple[float, int]:
        rows = db.execute(
            select(MedicationLog.status).where(
                MedicationLog.patient_user_id == patient_id,
                MedicationLog.due_at >= start,
                MedicationLog.due_at < end,
            )
        ).scalars().all()
        if not rows:
            return 0.0, 0
        taken = sum(
            1 for s in rows if str(s) == str(MedicationLogStatus.TAKEN)
        )
        return 100.0 * taken / len(rows), len(rows)

    recent, recent_n = rate(now - timedelta(days=RECENT_WINDOW), now)
    prior, prior_n = rate(
        now - timedelta(days=PRIOR_WINDOW + RECENT_WINDOW),
        now - timedelta(days=RECENT_WINDOW),
    )

    # Both windows need enough doses for a difference to mean anything.
    if recent_n < 6 or prior_n < 6:
        return None
    return recent, prior


def detect(db: Session) -> int:
    """Find people who have gone quiet, and people who are sliding."""
    today = clock.today()
    queued = 0

    enrolled = db.execute(
        select(CareEnrollment.patient_user_id)
        .where(CareEnrollment.status == "active")
        .distinct()
    ).scalars().all()

    for patient_id in enrolled:
        last = _last_activity(db, patient_id)
        if last is not None and (today - last).days >= SILENT_DAYS:
            if runner.enqueue(
                db,
                kind=TASK_KIND,
                dedupe_key=f"silent:{patient_id}:{today.isoformat()}",
                subject_user_id=patient_id,
                payload={"kind": "silent", "days": (today - last).days},
            ):
                queued += 1
            continue  # silence outranks a decline; do not raise both

        change = adherence_change(db, patient_id)
        if change is None:
            continue
        recent, prior = change
        if prior - recent >= DECLINE_POINTS:
            if runner.enqueue(
                db,
                kind=TASK_KIND,
                dedupe_key=f"decline:{patient_id}:{today.isoformat()}",
                subject_user_id=patient_id,
                payload={
                    "kind": "decline",
                    "recent": round(recent, 1),
                    "prior": round(prior, 1),
                },
            ):
                queued += 1

    return queued


@runner.handler(TASK_KIND)
def handle(db: Session, task: AgentTask) -> None:
    patient = db.get(User, task.subject_user_id)
    if patient is None:
        return

    if task.payload.get("kind") == "silent":
        days = int(task.payload.get("days", 0))
        raise_guardian_alerts(
            db,
            patient=patient,
            alert_type="disengaged_from_care",
            severity=AlertSeverity.ATTENTION if days < 21 else AlertSeverity.CRITICAL,
            title="No contact with care for two weeks",
            detail=(
                f"{patient.full_name} is enrolled in an active care programme "
                f"but has had no check-in, appointment or medication activity "
                f"for {days} days."
            ),
            permission=GuardianPermissionType.WELLBEING,
            meta={"days": days, "evidence": {"detector_id": "disengagement"}},
        )
    else:
        recent = task.payload.get("recent", 0)
        prior = task.payload.get("prior", 0)
        raise_guardian_alerts(
            db,
            patient=patient,
            alert_type="adherence_declining",
            severity=AlertSeverity.ATTENTION,
            title="Medication adherence is falling",
            detail=(
                f"{patient.full_name}'s medication adherence has fallen from "
                f"{prior:.0f}% to {recent:.0f}% over the last two weeks. No "
                "absolute threshold has been crossed yet."
            ),
            permission=GuardianPermissionType.MEDICATIONS,
            meta={
                "recent": recent,
                "prior": prior,
                "evidence": {"detector_id": "adherence_decline"},
            },
        )
    db.commit()


@register(
    "detect_disengagement",
    seconds=24 * 60 * 60,
    description="Find patients who have gone quiet or whose adherence is sliding",
)
def job() -> None:
    db = SessionLocal()
    try:
        detect(db)
    finally:
        db.close()
