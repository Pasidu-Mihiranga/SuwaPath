"""Slot generation and availability lookup.

Bookable slots are derived from a doctor's recurring `DoctorSchedule` blocks
minus appointments that already occupy them, rather than being pre-materialised
rows. That keeps the schedule editable by hospital admins without a migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.care import Appointment
from app.models.enums import AppointmentStatus, VisitType
from app.models.providers import Doctor, DoctorSchedule

# Statuses that still occupy a slot; cancelled/no-show free it again.
OCCUPYING_STATUSES = (
    AppointmentStatus.PENDING,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.CHECKED_IN,
    AppointmentStatus.IN_CONSULTATION,
    AppointmentStatus.COMPLETED,
)


@dataclass
class Slot:
    doctor_id: str
    hospital_id: str | None
    start: datetime
    end: datetime
    visit_type: VisitType

    def to_dict(self) -> dict:
        return {
            "doctor_id": self.doctor_id,
            "hospital_id": self.hospital_id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "visit_type": str(self.visit_type),
            "label": self.start.strftime("%I:%M %p").lstrip("0"),
            "date_label": self.start.strftime("%a, %d %b"),
        }


def _combine(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=timezone.utc)


def generate_slots_for_doctor(
    db: Session,
    doctor: Doctor,
    *,
    start_date: date | None = None,
    days: int = 14,
    visit_type: VisitType | None = None,
    limit: int | None = None,
) -> list[Slot]:
    """Expand schedule blocks into free slots over the next `days` days."""
    start_date = start_date or datetime.now(timezone.utc).date()
    end_date = start_date + timedelta(days=days)

    schedules = [s for s in doctor.schedules if s.is_active]
    if not schedules:
        return []

    booked = _booked_starts(db, doctor.id, start_date, end_date)
    now = datetime.now(timezone.utc)
    slots: list[Slot] = []

    for offset in range(days):
        day = start_date + timedelta(days=offset)
        for schedule in schedules:
            if schedule.day_of_week != day.weekday():
                continue
            if visit_type and str(schedule.visit_type) != str(visit_type):
                continue

            cursor = _combine(day, schedule.start_time)
            block_end = _combine(day, schedule.end_time)
            step = timedelta(minutes=schedule.slot_duration_minutes)
            issued = 0

            while cursor + step <= block_end and issued < schedule.max_patients:
                if cursor > now and cursor not in booked:
                    slots.append(
                        Slot(
                            doctor_id=doctor.id,
                            hospital_id=schedule.hospital_id or doctor.hospital_id,
                            start=cursor,
                            end=cursor + step,
                            visit_type=VisitType(str(schedule.visit_type)),
                        )
                    )
                    if limit and len(slots) >= limit:
                        slots.sort(key=lambda s: s.start)
                        return slots[:limit]
                cursor += step
                issued += 1

    slots.sort(key=lambda s: s.start)
    return slots[:limit] if limit else slots


def next_available_slot(
    db: Session, doctor: Doctor, *, visit_type: VisitType | None = None
) -> Slot | None:
    slots = generate_slots_for_doctor(db, doctor, days=21, visit_type=visit_type, limit=1)
    return slots[0] if slots else None


def next_available_map(
    db: Session, doctors: list[Doctor], *, days: int = 21
) -> dict[str, Slot]:
    """Earliest free slot per doctor, in one pass over their appointments.

    Avoids the N+1 query that per-doctor lookups would cause when ranking.
    """
    if not doctors:
        return {}

    start_date = datetime.now(timezone.utc).date()
    end_date = start_date + timedelta(days=days)
    doctor_ids = [d.id for d in doctors]

    rows = db.execute(
        select(Appointment.doctor_id, Appointment.scheduled_start).where(
            and_(
                Appointment.doctor_id.in_(doctor_ids),
                Appointment.scheduled_start >= _combine(start_date, time.min),
                Appointment.scheduled_start <= _combine(end_date, time.max),
                Appointment.status.in_([str(s) for s in OCCUPYING_STATUSES]),
            )
        )
    ).all()

    booked_by_doctor: dict[str, set[datetime]] = {}
    for doctor_id, start in rows:
        booked_by_doctor.setdefault(doctor_id, set()).add(_as_utc(start))

    now = datetime.now(timezone.utc)
    result: dict[str, Slot] = {}

    for doctor in doctors:
        booked = booked_by_doctor.get(doctor.id, set())
        schedules = [s for s in doctor.schedules if s.is_active]
        if not schedules:
            continue

        found: Slot | None = None
        for offset in range(days):
            if found:
                break
            day = start_date + timedelta(days=offset)
            for schedule in sorted(schedules, key=lambda s: s.start_time):
                if schedule.day_of_week != day.weekday():
                    continue
                cursor = _combine(day, schedule.start_time)
                block_end = _combine(day, schedule.end_time)
                step = timedelta(minutes=schedule.slot_duration_minutes)
                while cursor + step <= block_end:
                    if cursor > now and cursor not in booked:
                        found = Slot(
                            doctor_id=doctor.id,
                            hospital_id=schedule.hospital_id or doctor.hospital_id,
                            start=cursor,
                            end=cursor + step,
                            visit_type=VisitType(str(schedule.visit_type)),
                        )
                        break
                    cursor += step
                if found:
                    break
        if found:
            result[doctor.id] = found

    return result


def is_slot_free(
    db: Session, doctor_id: str, start: datetime, end: datetime
) -> bool:
    """True when no occupying appointment overlaps [start, end)."""
    clash = db.execute(
        select(Appointment.id)
        .where(
            and_(
                Appointment.doctor_id == doctor_id,
                Appointment.status.in_([str(s) for s in OCCUPYING_STATUSES]),
                Appointment.scheduled_start < end,
                Appointment.scheduled_end > start,
            )
        )
        .limit(1)
    ).first()
    return clash is None


def slot_matches_schedule(doctor: Doctor, start: datetime, end: datetime) -> bool:
    """Guard against booking outside the doctor's published hours."""
    start = _as_utc(start)
    for schedule in doctor.schedules:
        if not schedule.is_active or schedule.day_of_week != start.weekday():
            continue
        block_start = _combine(start.date(), schedule.start_time)
        block_end = _combine(start.date(), schedule.end_time)
        if block_start <= start and _as_utc(end) <= block_end:
            return True
    return False


def slot_duration_for(doctor: Doctor, start: datetime) -> int | None:
    """The doctor's own slot length for the block containing ``start``.

    Offered slots are generated from ``schedule.slot_duration_minutes``, so a
    booking must use the same length. Assuming a fixed duration instead made
    a 15-minute slot become a 20-minute appointment that ran into the next
    slot — and if that one was taken, the patient was told the slot they had
    just been offered was "already taken".

    Returns None when ``start`` falls outside every published block; the
    caller's schedule check reports that more precisely.
    """
    start = _as_utc(start)
    for schedule in doctor.schedules:
        if not schedule.is_active or schedule.day_of_week != start.weekday():
            continue
        block_start = _combine(start.date(), schedule.start_time)
        block_end = _combine(start.date(), schedule.end_time)
        if block_start <= start < block_end:
            return schedule.slot_duration_minutes
    return None


def _booked_starts(
    db: Session, doctor_id: str, start_date: date, end_date: date
) -> set[datetime]:
    rows = db.execute(
        select(Appointment.scheduled_start).where(
            and_(
                Appointment.doctor_id == doctor_id,
                Appointment.scheduled_start >= _combine(start_date, time.min),
                Appointment.scheduled_start <= _combine(end_date, time.max),
                Appointment.status.in_([str(s) for s in OCCUPYING_STATUSES]),
            )
        )
    ).scalars()
    return {_as_utc(row) for row in rows}


def _as_utc(value: datetime) -> datetime:
    """Normalise to timezone-aware UTC for comparison with generated slots."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
