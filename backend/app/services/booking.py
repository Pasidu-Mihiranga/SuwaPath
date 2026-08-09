"""Creating an appointment — the only implementation.

There are now two ways an appointment gets booked: a person taps Book, or a
person approves an action the system proposed. Those must not be two code
paths. Guardian booking consent (`can_book_appointments`) is checked in exactly
one place today, and duplicating this logic to serve the approval endpoint is
precisely how that check gets subtly weakened in one copy and nobody notices.

So the router and the approval endpoint both call `create_appointment`, and
neither is allowed its own version of the rules.

`HTTPException` is raised from here rather than a domain error type. That is
deliberate: the status codes are already part of the API contract and asserted
by the scenario suite, and the job layer catches them perfectly well. Inventing
a parallel error hierarchy to translate back into the same codes would add a
layer that can disagree with itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_patient_profile, get_relationship, require_permission
from app.models.care import Appointment
from app.models.clinical import Recommendation
from app.models.enums import (
    AppointmentStatus,
    GuardianPermissionType,
    NotificationCategory,
    NotificationPriority,
    UrgencyLevel,
    UserRole,
    VisitType,
)
from app.models.identity import User
from app.models.platform import Notification
from app.models.providers import Doctor
from app.services.availability import is_slot_free, slot_duration_for, slot_matches_schedule
from app.services.matching import haversine_km


def resolve_booking_patient(
    db: Session, current_user: User, requested_patient_id: str | None
) -> User:
    """Determine who the appointment is for, enforcing guardian consent."""
    role = str(current_user.role)

    if role == str(UserRole.PATIENT):
        if requested_patient_id and requested_patient_id != current_user.id:
            raise HTTPException(
                status_code=403, detail="You can only book appointments for yourself."
            )
        return current_user

    if role == str(UserRole.GUARDIAN):
        if not requested_patient_id:
            raise HTTPException(
                status_code=400,
                detail="Specify which dependent this appointment is for.",
            )
        relationship = get_relationship(db, current_user.id, requested_patient_id)
        require_permission(relationship, GuardianPermissionType.APPOINTMENTS)
        if not relationship.can_book_appointments:
            raise HTTPException(
                status_code=403,
                detail=(
                    "You have view access to appointments but have not been "
                    "authorised to book on this person's behalf."
                ),
            )
        patient = db.get(User, requested_patient_id)
        if patient is None:
            raise HTTPException(status_code=404, detail="Dependent not found.")
        return patient

    raise HTTPException(
        status_code=403, detail="Only patients and authorised guardians can book."
    )


def load_bookable_doctor(db: Session, doctor_id: str) -> Doctor:
    doctor = db.execute(
        select(Doctor)
        .options(
            selectinload(Doctor.schedules),
            selectinload(Doctor.user),
            selectinload(Doctor.specialty),
            selectinload(Doctor.hospital),
        )
        .where(Doctor.id == doctor_id)
    ).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    if not doctor.accepts_new_patients:
        raise HTTPException(
            status_code=409, detail="This doctor is not accepting new appointments."
        )
    return doctor


def create_appointment(
    db: Session,
    *,
    actor: User,
    patient: User,
    doctor_id: str,
    scheduled_start: datetime,
    visit_type: VisitType,
    duration_minutes: int | None = None,
    reason: str | None = None,
    recommendation_id: str | None = None,
    commit: bool = True,
) -> Appointment:
    """Book an appointment, or raise HTTPException explaining why not.

    Every availability check runs here rather than at the caller, because an
    approval may arrive hours after the proposal was written and the slot it
    named may be long gone. Freshness has to be re-established at the moment of
    writing, never inherited from whatever the proposal recorded.
    """
    doctor = load_bookable_doctor(db, doctor_id)

    start = scheduled_start
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if start <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Choose a future appointment time.")

    # Default to the doctor's own slot length rather than a fixed guess, so a
    # booking occupies exactly the slot that was offered.
    duration = duration_minutes or slot_duration_for(doctor, start) or 20
    end = start + timedelta(minutes=duration)

    if not slot_matches_schedule(doctor, start, end):
        raise HTTPException(
            status_code=409,
            detail="That time is outside the doctor's published clinic hours.",
        )
    if not is_slot_free(db, doctor.id, start, end):
        raise HTTPException(
            status_code=409, detail="That slot has just been taken. Choose another time."
        )
    if visit_type == VisitType.TELECONSULTATION and not doctor.supports_teleconsultation:
        raise HTTPException(
            status_code=409, detail="This doctor does not offer teleconsultation."
        )

    recommendation = None
    urgency = UrgencyLevel.ROUTINE
    if recommendation_id:
        recommendation = db.get(Recommendation, recommendation_id)
        if recommendation and recommendation.patient_user_id == patient.id:
            urgency = UrgencyLevel(str(recommendation.urgency))
        else:
            # A recommendation belonging to someone else must not travel with
            # this booking, whatever the caller passed.
            recommendation = None

    profile = get_patient_profile(db, patient.id)
    distance = None
    if profile and doctor.hospital and profile.latitude and profile.longitude:
        distance = round(
            haversine_km(
                profile.latitude, profile.longitude,
                doctor.hospital.latitude, doctor.hospital.longitude,
            ),
            1,
        )

    now = datetime.now(timezone.utc)
    appointment = Appointment(
        patient_user_id=patient.id,
        doctor_id=doctor.id,
        hospital_id=doctor.hospital_id,
        booked_by_user_id=actor.id,
        recommendation_id=recommendation.id if recommendation else None,
        scheduled_start=start,
        scheduled_end=end,
        visit_type=visit_type,
        urgency=urgency,
        status=AppointmentStatus.CONFIRMED,
        reason=reason,
        chief_complaint=(recommendation.care_category if recommendation else reason),
        fee_lkr=(
            doctor.teleconsultation_fee_lkr
            if visit_type == VisitType.TELECONSULTATION
            else doctor.consultation_fee_lkr
        ),
        booked_at=now,
        confirmed_at=now,
        patient_distance_km=distance,
        teleconsultation_url=(
            f"https://meet.suwapath.lk/consult/{doctor.id[:8]}"
            if visit_type == VisitType.TELECONSULTATION
            else None
        ),
    )
    db.add(appointment)
    db.flush()

    when = start.strftime("%d %b at %I:%M %p").replace(" 0", " ")
    db.add(
        Notification(
            user_id=patient.id,
            category=NotificationCategory.APPOINTMENT,
            priority=NotificationPriority.NORMAL,
            title="Appointment confirmed",
            body=(
                f"Your appointment with {doctor.user.full_name} is confirmed for "
                f"{when}."
            ),
            action_type="appointment",
            action_id=appointment.id,
        )
    )
    # Reminder event, surfaced by the notifications endpoint when it falls due.
    db.add(
        Notification(
            user_id=patient.id,
            category=NotificationCategory.APPOINTMENT,
            priority=NotificationPriority.HIGH,
            title="Appointment reminder",
            body=f"Your appointment with {doctor.user.full_name} is tomorrow.",
            action_type="appointment",
            action_id=appointment.id,
            scheduled_for=start - timedelta(days=1),
        )
    )
    # The doctor sees the new booking in their queue.
    db.add(
        Notification(
            user_id=doctor.user_id,
            category=NotificationCategory.APPOINTMENT,
            priority=(
                NotificationPriority.HIGH
                if urgency in (UrgencyLevel.EMERGENCY, UrgencyLevel.URGENT)
                else NotificationPriority.NORMAL
            ),
            title="New appointment booked",
            body=f"{patient.full_name} booked a consultation for {when}.",
            action_type="appointment",
            action_id=appointment.id,
            about_patient_user_id=patient.id,
        )
    )

    if commit:
        db.commit()
        db.refresh(appointment)
    return appointment
