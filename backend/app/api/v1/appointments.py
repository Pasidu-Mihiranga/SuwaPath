"""Appointment booking and lifecycle.

One `Appointment` row is shared by the patient, the doctor's queue and the
hospital's operational analytics — booking here makes the patient appear in the
doctor's live queue immediately (Scenario A).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_relationship, require_permission
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.care import Appointment
from app.models.enums import (
    AppointmentStatus,
    GuardianPermissionType,
    NotificationCategory,
    NotificationPriority,
    UserRole,
    VisitType,
)
from app.models.identity import User
from app.models.platform import Notification
from app.models.providers import Doctor
from app.services.availability import (
    is_slot_free,
    slot_matches_schedule,
)
from app.services import booking

router = APIRouter(prefix="/appointments", tags=["appointments"])


# Legal status transitions. Anything else is rejected with a clear message.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    str(AppointmentStatus.PENDING): {
        str(AppointmentStatus.CONFIRMED), str(AppointmentStatus.CANCELLED)
    },
    str(AppointmentStatus.CONFIRMED): {
        str(AppointmentStatus.CHECKED_IN), str(AppointmentStatus.CANCELLED),
        str(AppointmentStatus.NO_SHOW),
    },
    str(AppointmentStatus.CHECKED_IN): {
        str(AppointmentStatus.IN_CONSULTATION), str(AppointmentStatus.CANCELLED),
    },
    str(AppointmentStatus.IN_CONSULTATION): {str(AppointmentStatus.COMPLETED)},
    str(AppointmentStatus.COMPLETED): set(),
    str(AppointmentStatus.CANCELLED): set(),
    str(AppointmentStatus.NO_SHOW): set(),
}


class BookRequest(BaseModel):
    doctor_id: str
    scheduled_start: datetime
    visit_type: VisitType = VisitType.PHYSICAL
    reason: str | None = Field(default=None, max_length=1000)
    recommendation_id: str | None = None
    # Guardians booking on behalf of a dependent.
    patient_user_id: str | None = None
    duration_minutes: int | None = None


class StatusRequest(BaseModel):
    status: AppointmentStatus
    reason: str | None = None


def _serialise(appointment: Appointment, *, include_patient: bool = False) -> dict:
    doctor = appointment.doctor
    data = {
        "id": appointment.id,
        "status": str(appointment.status),
        "visit_type": str(appointment.visit_type),
        "urgency": str(appointment.urgency),
        "scheduled_start": appointment.scheduled_start,
        "scheduled_end": appointment.scheduled_end,
        "reason": appointment.reason,
        "chief_complaint": appointment.chief_complaint,
        "fee_lkr": appointment.fee_lkr,
        "teleconsultation_url": appointment.teleconsultation_url,
        "distance_km": appointment.patient_distance_km,
        "recommendation_id": appointment.recommendation_id,
        "doctor": {
            "doctor_id": doctor.id if doctor else None,
            "name": doctor.user.full_name if doctor and doctor.user else None,
            "specialty_name": doctor.specialty.name if doctor and doctor.specialty else None,
            "sub_specialty": doctor.sub_specialty if doctor else None,
        },
        "hospital": {
            "hospital_id": appointment.hospital.id if appointment.hospital else None,
            "name": appointment.hospital.name if appointment.hospital else None,
            "address": appointment.hospital.address if appointment.hospital else None,
            "city": appointment.hospital.city if appointment.hospital else None,
        },
        "booked_at": appointment.booked_at,
        "confirmed_at": appointment.confirmed_at,
        "checked_in_at": appointment.checked_in_at,
        "completed_at": appointment.completed_at,
        "cancelled_at": appointment.cancelled_at,
        "cancellation_reason": appointment.cancellation_reason,
    }
    if include_patient and appointment.patient:
        data["patient"] = {
            "user_id": appointment.patient.id,
            "name": appointment.patient.full_name,
        }
    return data


# Booking rules live in services/booking.py so that the action-approval
# endpoint executes the identical checks. Aliased here for the existing
# call sites in this module.
_resolve_booking_patient = booking.resolve_booking_patient


@router.post("", status_code=status.HTTP_201_CREATED)
def book(
    payload: BookRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    patient = booking.resolve_booking_patient(db, current_user, payload.patient_user_id)
    appointment = booking.create_appointment(
        db,
        actor=current_user,
        patient=patient,
        doctor_id=payload.doctor_id,
        scheduled_start=payload.scheduled_start,
        visit_type=payload.visit_type,
        duration_minutes=payload.duration_minutes,
        reason=payload.reason,
        recommendation_id=payload.recommendation_id,
    )
    return _serialise(appointment, include_patient=True)


@router.get("")
def list_appointments(
    scope: str = Query(default="upcoming", pattern="^(upcoming|past|all)$"),
    patient_user_id: str | None = None,
    limit: int = Query(default=50, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    role = str(current_user.role)
    now = datetime.now(timezone.utc)

    stmt = select(Appointment).options(
        selectinload(Appointment.doctor).selectinload(Doctor.user),
        selectinload(Appointment.doctor).selectinload(Doctor.specialty),
        selectinload(Appointment.hospital),
        selectinload(Appointment.patient),
    )

    if role == str(UserRole.PATIENT):
        stmt = stmt.where(Appointment.patient_user_id == current_user.id)
    elif role == str(UserRole.GUARDIAN):
        if not patient_user_id:
            raise HTTPException(status_code=400, detail="Specify a dependent.")
        relationship = get_relationship(db, current_user.id, patient_user_id)
        require_permission(relationship, GuardianPermissionType.APPOINTMENTS)
        stmt = stmt.where(Appointment.patient_user_id == patient_user_id)
    elif role == str(UserRole.DOCTOR):
        doctor = db.execute(
            select(Doctor).where(Doctor.user_id == current_user.id)
        ).scalar_one_or_none()
        if doctor is None:
            raise HTTPException(status_code=404, detail="No doctor profile linked.")
        stmt = stmt.where(Appointment.doctor_id == doctor.id)
    elif role == str(UserRole.HOSPITAL_ADMIN):
        if not current_user.hospital_id:
            raise HTTPException(status_code=403, detail="Account not linked to a hospital.")
        stmt = stmt.where(Appointment.hospital_id == current_user.hospital_id)

    if scope == "upcoming":
        stmt = stmt.where(
            Appointment.scheduled_start >= now,
            Appointment.status.notin_(
                [str(AppointmentStatus.CANCELLED), str(AppointmentStatus.NO_SHOW)]
            ),
        ).order_by(Appointment.scheduled_start.asc())
    elif scope == "past":
        stmt = stmt.where(Appointment.scheduled_start < now).order_by(
            Appointment.scheduled_start.desc()
        )
    else:
        stmt = stmt.order_by(Appointment.scheduled_start.desc())

    rows = db.execute(stmt.limit(limit)).scalars().unique()
    include_patient = role in (
        str(UserRole.DOCTOR), str(UserRole.HOSPITAL_ADMIN),
        str(UserRole.SYSTEM_ADMIN), str(UserRole.GUARDIAN),
    )
    return [_serialise(a, include_patient=include_patient) for a in rows]


def _load_appointment(db: Session, appointment_id: str) -> Appointment:
    appointment = db.execute(
        select(Appointment)
        .options(
            selectinload(Appointment.doctor).selectinload(Doctor.user),
            selectinload(Appointment.doctor).selectinload(Doctor.specialty),
            selectinload(Appointment.hospital),
            selectinload(Appointment.patient),
        )
        .where(Appointment.id == appointment_id)
    ).scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return appointment


def _can_modify(db: Session, user: User, appointment: Appointment) -> bool:
    role = str(user.role)
    if role == str(UserRole.SYSTEM_ADMIN):
        return True
    if role == str(UserRole.PATIENT):
        return appointment.patient_user_id == user.id
    if role == str(UserRole.GUARDIAN):
        try:
            relationship = get_relationship(db, user.id, appointment.patient_user_id)
        except HTTPException:
            return False
        return relationship.can_book_appointments
    if role == str(UserRole.DOCTOR):
        doctor = db.execute(
            select(Doctor).where(Doctor.user_id == user.id)
        ).scalar_one_or_none()
        return bool(doctor and appointment.doctor_id == doctor.id)
    if role == str(UserRole.HOSPITAL_ADMIN):
        return appointment.hospital_id == user.hospital_id
    return False


@router.get("/{appointment_id}")
def get_appointment(
    appointment_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    appointment = _load_appointment(db, appointment_id)
    if not _can_modify(db, current_user, appointment):
        raise HTTPException(status_code=403, detail="You cannot view this appointment.")
    return _serialise(appointment, include_patient=True)


@router.patch("/{appointment_id}/status")
def update_status(
    appointment_id: str,
    payload: StatusRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    appointment = _load_appointment(db, appointment_id)
    if not _can_modify(db, current_user, appointment):
        raise HTTPException(status_code=403, detail="You cannot modify this appointment.")

    current = str(appointment.status)
    target = str(payload.status)
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot move an appointment from '{current}' to '{target}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_TRANSITIONS.get(current, set()))) or 'none'}."
            ),
        )

    # Clinical status changes belong to the care team, not the patient.
    clinical_states = {
        str(AppointmentStatus.CHECKED_IN),
        str(AppointmentStatus.IN_CONSULTATION),
        str(AppointmentStatus.COMPLETED),
        str(AppointmentStatus.NO_SHOW),
    }
    if target in clinical_states and str(current_user.role) in (
        str(UserRole.PATIENT), str(UserRole.GUARDIAN)
    ):
        raise HTTPException(
            status_code=403,
            detail="Only the care team can set this appointment status.",
        )

    now = datetime.now(timezone.utc)
    appointment.status = payload.status
    if payload.status == AppointmentStatus.CONFIRMED:
        appointment.confirmed_at = now
    elif payload.status == AppointmentStatus.CHECKED_IN:
        appointment.checked_in_at = now
    elif payload.status == AppointmentStatus.IN_CONSULTATION:
        appointment.started_at = now
    elif payload.status == AppointmentStatus.COMPLETED:
        appointment.completed_at = now
    elif payload.status == AppointmentStatus.CANCELLED:
        appointment.cancelled_at = now
        appointment.cancellation_reason = payload.reason
        db.add(
            Notification(
                user_id=appointment.patient_user_id,
                category=NotificationCategory.APPOINTMENT,
                priority=NotificationPriority.HIGH,
                title="Appointment cancelled",
                body=(
                    f"Your appointment on "
                    f"{appointment.scheduled_start.strftime('%d %b at %I:%M %p')} "
                    f"was cancelled."
                    + (f" Reason: {payload.reason}" if payload.reason else "")
                ),
                action_type="appointment",
                action_id=appointment.id,
            )
        )

    db.commit()
    db.refresh(appointment)
    return _serialise(appointment, include_patient=True)


class RescheduleRequest(BaseModel):
    scheduled_start: datetime
    duration_minutes: int | None = None


@router.patch("/{appointment_id}/reschedule")
def reschedule(
    appointment_id: str,
    payload: RescheduleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    appointment = _load_appointment(db, appointment_id)
    if not _can_modify(db, current_user, appointment):
        raise HTTPException(status_code=403, detail="You cannot modify this appointment.")
    if str(appointment.status) in (
        str(AppointmentStatus.COMPLETED), str(AppointmentStatus.CANCELLED)
    ):
        raise HTTPException(
            status_code=409, detail="This appointment can no longer be rescheduled."
        )

    start = payload.scheduled_start
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    duration = payload.duration_minutes or int(
        (appointment.scheduled_end - appointment.scheduled_start).total_seconds() / 60
    )
    end = start + timedelta(minutes=duration)

    doctor = db.execute(
        select(Doctor).options(selectinload(Doctor.schedules)).where(
            Doctor.id == appointment.doctor_id
        )
    ).scalar_one()

    if not slot_matches_schedule(doctor, start, end):
        raise HTTPException(
            status_code=409, detail="That time is outside the doctor's clinic hours."
        )
    if not is_slot_free(db, doctor.id, start, end):
        raise HTTPException(status_code=409, detail="That slot is already taken.")

    appointment.scheduled_start = start
    appointment.scheduled_end = end
    # Reschedule count is a real no-show predictor, so it is tracked.
    appointment.reschedule_count += 1
    appointment.status = AppointmentStatus.CONFIRMED
    appointment.confirmed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(appointment)
    return _serialise(appointment, include_patient=True)
