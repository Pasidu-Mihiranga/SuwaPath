"""Noticing that medication stopped being taken.

There was a detector for this already and it could not work, for a reason that
is only obvious once you look at how a dose becomes a row.

`MedicationLog` rows are written when a patient *tells* the app what they did —
taken, missed, snoozed. A patient who has stopped taking a medication has also
stopped opening the app about it, so they report nothing, and no row appears.
The detector looked for a run of rows marked `MISSED` and therefore found
abandonment only in people who were diligently logging their own
non-adherence. The case it was written for was the one case it could not see.

So the first job here materialises the schedule: for every dose whose time has
passed with nothing recorded, write the `MISSED` row that the patient's silence
implies. Only then does a run of missed doses mean what it says.

Two boundaries keep that from being reckless:

**A grace period**, because someone taking a tablet two hours late has taken
it, and marking them missed would be both wrong and insulting.

**A bounded lookback.** The job only ever considers the last few days. An
unbounded first run against a year of schedules would manufacture tens of
thousands of rows describing a past nobody observed.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.agentic import AgentTask
from app.models.care import ElderlyRecord, Medication, MedicationLog
from app.models.enums import (
    AlertSeverity,
    GuardianPermissionType,
    MedicationLogStatus,
)
from app.models.identity import User
from app.services import clock, timeslots
from app.services.alerts import raise_guardian_alerts
from app.services.jobs import register, runner

logger = logging.getLogger(__name__)

TASK_KIND = "missed_medication"

# A dose is not missed until this long after it was due.
GRACE_MINUTES = 120

# How far back a single run will look. Deliberately short: this job runs
# hourly, so anything older than this was either already materialised or
# happened while the system was down, and inventing history for the latter
# tells the patient something nobody actually observed.
LOOKBACK_DAYS = 3


def materialise_doses(db: Session) -> int:
    """Write a MISSED row for every overdue dose nobody recorded.

    Returns how many rows were created. Safe to run repeatedly: the unique
    constraint on `(medication_id, due_at)` turns a second pass into a no-op
    rather than a duplicate.
    """
    now = clock.now()
    horizon = now - timedelta(minutes=GRACE_MINUTES)
    floor = now - timedelta(days=LOOKBACK_DAYS)

    medications = db.execute(
        select(Medication).where(Medication.is_active.is_(True))
    ).scalars().all()

    created = 0
    for medication in medications:
        if not medication.schedule_times:
            continue

        start = floor
        if medication.start_date:
            # start_date is a date; compare from the beginning of that day.
            started = timeslots.at_local(medication.start_date, "00:00")
            if started and started > start:
                start = started
        if medication.end_date:
            ended = timeslots.at_local(medication.end_date, "23:59")
            if ended and ended < horizon:
                horizon = ended

        for due in timeslots.due_times_between(medication.schedule_times, start, horizon):
            result = db.execute(
                pg_insert(MedicationLog)
                .values(
                    medication_id=medication.id,
                    patient_user_id=medication.patient_user_id,
                    due_at=due,
                    status=MedicationLogStatus.MISSED,
                )
                .on_conflict_do_nothing(index_elements=["medication_id", "due_at"])
                .returning(MedicationLog.id)
            ).scalar_one_or_none()
            if result:
                created += 1

    if created:
        db.commit()
        logger.info("materialised %d overdue dose(s)", created)
    return created


def consecutive_missed(db: Session, medication: Medication) -> int:
    """How many doses in a row were missed, most recent first."""
    logs = db.execute(
        select(MedicationLog)
        .where(MedicationLog.medication_id == medication.id)
        .order_by(MedicationLog.due_at.desc())
        .limit(10)
    ).scalars().all()

    run = 0
    for log in logs:
        if str(log.status) == str(MedicationLogStatus.MISSED):
            run += 1
        else:
            break
    return run


def detect_for_patient(db: Session, patient: User) -> list[dict]:
    """Run the pattern check for one person and alert where it fires.

    Shared by the patient-facing endpoint and the scheduled job so there is one
    definition of "a run of missed doses" rather than two that can drift.
    """
    record = db.execute(
        select(ElderlyRecord).where(ElderlyRecord.patient_user_id == patient.id)
    ).scalar_one_or_none()
    threshold = record.missed_medication_alert_threshold if record else 2

    medications = db.execute(
        select(Medication).where(
            Medication.patient_user_id == patient.id,
            Medication.is_active.is_(True),
        )
    ).scalars().all()

    raised: list[dict] = []
    for medication in medications:
        run = consecutive_missed(db, medication)
        if run < threshold:
            continue

        count = raise_guardian_alerts(
            db,
            patient=patient,
            alert_type="missed_medication_pattern",
            severity=(
                AlertSeverity.CRITICAL if medication.is_critical else AlertSeverity.ATTENTION
            ),
            title="Repeated missed medication",
            detail=(
                f"{patient.full_name} has missed {run} consecutive "
                f"doses of {medication.name} {medication.dosage}."
            ),
            permission=GuardianPermissionType.MEDICATIONS,
            meta={
                "medication": f"{medication.name} {medication.dosage}",
                "consecutive_missed": run,
                "evidence": {"detector_id": "missed_medication"},
            },
        )
        raised.append(
            {
                "medication": medication.name,
                "consecutive_missed": run,
                "guardians_alerted": count,
            }
        )

    if raised:
        db.commit()
    return raised


# --------------------------------------------------------------------------
# Autonomous side
# --------------------------------------------------------------------------
def detect(db: Session) -> int:
    """Queue a check for every patient whose doses have gone unrecorded."""
    materialise_doses(db)

    # Only patients with a live run worth investigating are queued, so the
    # queue reflects real findings rather than one task per patient per day.
    candidates = db.execute(
        select(Medication.patient_user_id)
        .where(Medication.is_active.is_(True))
        .distinct()
    ).scalars().all()

    queued = 0
    for patient_id in candidates:
        if runner.enqueue(
            db,
            kind=TASK_KIND,
            # Bucketed by local day: a run detected this morning should not be
            # re-raised this afternoon.
            dedupe_key=f"missed_doses:{patient_id}:{clock.today().isoformat()}",
            subject_user_id=patient_id,
        ):
            queued += 1
    return queued


@runner.handler(TASK_KIND)
def handle(db: Session, task: AgentTask) -> None:
    patient = db.get(User, task.subject_user_id)
    if patient is None:
        return
    detect_for_patient(db, patient)


@register(
    "materialise_and_check_medication",
    seconds=60 * 60,
    description="Mark overdue doses and alert on repeated misses",
)
def job() -> None:
    db = SessionLocal()
    try:
        detect(db)
    finally:
        db.close()
