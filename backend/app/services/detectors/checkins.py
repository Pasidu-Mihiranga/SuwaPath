"""Noticing that someone stopped checking in.

`ElderlyRecord.consecutive_missed_checkins` has existed since the beginning and
is only ever *reset* to zero — when a check-in arrives. Nothing has ever
incremented it, so the guardian dashboard's "No check-in for N days" badge
reads zero for everyone who has ever checked in once, and the threshold column
beside it (`missed_checkin_alert_threshold`) is read by no code at all.

The counter is not a bug in isolation; it is the visible half of a missing
job. Absence cannot be reported by the person who is absent, so something has
to look for the gap. That is this file.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.agentic import AgentTask
from app.models.care import DailyCheckIn, ElderlyRecord
from app.models.enums import AlertSeverity, GuardianPermissionType
from app.models.identity import User
from app.services import clock
from app.services.alerts import raise_guardian_alerts
from app.services.jobs import register, runner

logger = logging.getLogger(__name__)

TASK_KIND = "checkin_lapse"


def days_since_checkin(db: Session, patient_user_id: str) -> int | None:
    """Whole local days since the last check-in, or None if there never was one."""
    last = db.execute(
        select(DailyCheckIn.check_in_date)
        .where(DailyCheckIn.patient_user_id == patient_user_id)
        .order_by(DailyCheckIn.check_in_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last is None:
        return None
    return (clock.today() - last).days


def detect(db: Session) -> int:
    """Update the missed-check-in counter and queue alerts where it breaches.

    The counter is written here rather than in the handler because it is a
    *fact about the data*, not an action: the guardian dashboard should show
    the right number whether or not an alert was ever raised.
    """
    # ElderlyRecord has no active flag — enrolment is what creates the row,
    # and it is removed when the programme ends.
    records = db.execute(select(ElderlyRecord)).scalars().all()

    queued = 0
    changed = False

    for record in records:
        gap = days_since_checkin(db, record.patient_user_id)
        if gap is None:
            # Never checked in. Enrolment date would be the fair baseline, and
            # without one there is nothing to measure a lapse against, so this
            # stays silent rather than alerting about a programme someone only
            # joined yesterday.
            continue

        if record.consecutive_missed_checkins != gap:
            record.consecutive_missed_checkins = gap
            changed = True

        threshold = record.missed_checkin_alert_threshold or 2
        if gap < threshold:
            continue

        if runner.enqueue(
            db,
            kind=TASK_KIND,
            # Bucketed per day, so a lapse that persists for a week produces
            # one alert a day at most rather than one per run.
            dedupe_key=f"checkin_lapse:{record.patient_user_id}:{clock.today().isoformat()}",
            subject_user_id=record.patient_user_id,
            payload={"days": gap, "threshold": threshold},
        ):
            queued += 1

    if changed:
        db.commit()
    return queued


@runner.handler(TASK_KIND)
def handle(db: Session, task: AgentTask) -> None:
    patient = db.get(User, task.subject_user_id)
    if patient is None:
        return

    days = int(task.payload.get("days", 0))
    raise_guardian_alerts(
        db,
        patient=patient,
        alert_type="missed_checkin",
        severity=AlertSeverity.ATTENTION if days < 5 else AlertSeverity.CRITICAL,
        title="No recent check-in",
        detail=(
            f"{patient.full_name} has not completed a daily check-in for "
            f"{days} day(s)."
        ),
        permission=GuardianPermissionType.WELLBEING,
        meta={"days": days, "evidence": {"detector_id": "checkin_lapse"}},
    )
    db.commit()


@register(
    "detect_checkin_lapses",
    seconds=12 * 60 * 60,
    description="Track missed daily check-ins and alert guardians",
)
def job() -> None:
    db = SessionLocal()
    try:
        detect(db)
    finally:
        db.close()
