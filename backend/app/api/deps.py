"""Shared API dependencies and access-control helpers.

Centralises the three access rules that appear across many endpoints:
  * guardians see only what the patient consented to (internal rule 6)
  * doctors see only patients booked with or referred to them (rule 8)
  * hospital admins are scoped to their own facility (rule 9)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import audit
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.care import Appointment, Referral
from app.models.enums import GuardianPermissionType, UserRole
from app.models.identity import (
    BreakGlassAccess,
    GuardianRelationship,
    PatientProfile,
    User,
)
from app.models.providers import Doctor


def get_patient_profile(db: Session, user_id: str) -> PatientProfile | None:
    return db.execute(
        select(PatientProfile).where(PatientProfile.user_id == user_id)
    ).scalar_one_or_none()


def build_patient_context(db: Session, user_id: str) -> dict:
    """Clinical context passed to the AI graph and the red-flag engine."""
    profile = get_patient_profile(db, user_id)
    if not profile:
        return {}

    from app.models.care import MaternalRecord

    maternal = db.execute(
        select(MaternalRecord).where(MaternalRecord.patient_user_id == user_id)
    ).scalar_one_or_none()

    return {
        "age": profile.age,
        "sex": str(profile.sex) if profile.sex else None,
        "is_pregnant": bool(profile.is_pregnant),
        "is_postpartum": bool(maternal.is_postpartum) if maternal else False,
        "pregnancy_week": maternal.pregnancy_week if maternal else None,
        "chronic_conditions": profile.chronic_conditions or [],
        "current_medications": profile.current_medications or [],
        "allergies": profile.allergies or [],
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "city": profile.city,
    }


# --------------------------------------------------------------------------
# Guardian consent
# --------------------------------------------------------------------------
def get_relationship(
    db: Session, guardian_user_id: str, patient_user_id: str
) -> GuardianRelationship:
    relationship = db.execute(
        select(GuardianRelationship)
        .options(selectinload(GuardianRelationship.permissions))
        .where(
            GuardianRelationship.guardian_user_id == guardian_user_id,
            GuardianRelationship.patient_user_id == patient_user_id,
            GuardianRelationship.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if relationship is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not registered as a guardian for this person.",
        )
    return relationship


def require_permission(
    relationship: GuardianRelationship, permission: GuardianPermissionType
) -> None:
    """Enforce a consent scope. FULL_MEDICAL implies every other scope."""
    granted = relationship.granted_scopes()
    if GuardianPermissionType.FULL_MEDICAL in granted or permission in granted:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"The patient has not granted access to '{permission}'. "
            f"They can enable this in their sharing settings."
        ),
    )


def resolve_patient_access(
    db: Session,
    current_user: User,
    patient_user_id: str,
    *,
    permission: GuardianPermissionType | None = None,
) -> User:
    """Resolve read access to a patient record for the calling user.

    Patients reach their own record; guardians reach a dependent's within the
    granted scope; doctors reach patients they have a care relationship with;
    system admins reach anything.

    Every access by someone who is *not* the patient is written to the audit
    trail. Recording only writes is the common shortcut and the wrong one:
    the harm in a medical system is usually someone reading a record they had
    no business opening, and a log of modifications cannot show that. HIPAA
    §164.312(b) asks for access logging for the same reason.

    Self-access is deliberately not logged. It is not a disclosure, and
    recording it would bury the entries that matter under routine noise.
    """
    if current_user.id == patient_user_id:
        return current_user

    patient, basis = _authorise_other(db, current_user, patient_user_id, permission)

    # One exit, one audit entry. The four authorisation paths used to return
    # independently, and logging in each would have meant four chances to add
    # a fifth later and forget.
    #
    # This commits, which is safe because access is resolved at the top of a
    # handler before anything has been changed. It is deliberate rather than
    # incidental: joining the caller's transaction would mean a read-only
    # endpoint — which never commits — silently discarding its own access log,
    # and an access log that disappears on the paths that only read is worse
    # than none, because those are exactly the accesses worth recording.
    audit.record(
        db,
        action="phi.read",
        actor_user_id=current_user.id,
        actor_role=str(current_user.role),
        resource_type="patient",
        resource_id=patient_user_id,
        detail={"basis": basis, "permission": str(permission) if permission else None},
    )
    return patient


def _authorise_other(
    db: Session,
    current_user: User,
    patient_user_id: str,
    permission: GuardianPermissionType | None,
) -> tuple[User, str]:
    """Authorise access to someone else's record. Returns (patient, basis).

    `basis` names *why* access was allowed, so the audit trail answers the
    question an investigator actually asks — not merely that a doctor read a
    record, but under which relationship they were entitled to.
    """
    role = str(current_user.role)

    if role == str(UserRole.SYSTEM_ADMIN):
        return _load_patient(db, patient_user_id), "system_admin"

    if role == str(UserRole.GUARDIAN):
        relationship = get_relationship(db, current_user.id, patient_user_id)
        if permission:
            require_permission(relationship, permission)
        return _load_patient(db, patient_user_id), "guardian_consent"

    if role == str(UserRole.DOCTOR):
        if doctor_has_patient(db, current_user.id, patient_user_id):
            return _load_patient(db, patient_user_id), "care_relationship"
        if _break_glass_active(db, current_user.id, patient_user_id):
            return _load_patient(db, patient_user_id), "break_glass"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You can only access patients who are booked with you or "
                "have been referred to you."
            ),
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to this patient record.",
    )


BREAK_GLASS_HOURS = 4


def _break_glass_active(db: Session, actor_user_id: str, patient_user_id: str) -> bool:
    """Is there a live emergency override for this clinician and patient?"""
    return db.execute(
        select(BreakGlassAccess.id).where(
            BreakGlassAccess.actor_user_id == actor_user_id,
            BreakGlassAccess.patient_user_id == patient_user_id,
            BreakGlassAccess.expires_at > datetime.now(timezone.utc),
        )
    ).first() is not None


def declare_break_glass(
    db: Session, *, actor: User, patient_user_id: str, reason: str
) -> BreakGlassAccess:
    """Open an emergency override, and say so in the audit trail.

    Time-boxed rather than open-ended: an override that lasts until someone
    remembers to close it lasts forever. Four hours covers a shift handover
    and expires on its own.
    """
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Emergency access needs a written clinical reason. It is "
                "recorded against your name and reviewed afterwards."
            ),
        )
    if str(actor.role) not in (str(UserRole.DOCTOR), str(UserRole.SYSTEM_ADMIN)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only clinicians can use emergency access.",
        )

    grant = BreakGlassAccess(
        actor_user_id=actor.id,
        patient_user_id=patient_user_id,
        reason=reason,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=BREAK_GLASS_HOURS),
    )
    db.add(grant)
    audit.record(
        db,
        action="phi.break_glass_declared",
        actor_user_id=actor.id,
        actor_role=str(actor.role),
        resource_type="patient",
        resource_id=patient_user_id,
        detail={"reason": reason, "expires_in_hours": BREAK_GLASS_HOURS},
    )
    return grant


def _load_patient(db: Session, patient_user_id: str) -> User:
    patient = db.get(User, patient_user_id)
    if patient is None or str(patient.role) != str(UserRole.PATIENT):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found."
        )
    return patient


# --------------------------------------------------------------------------
# Doctor scope
# --------------------------------------------------------------------------
def get_doctor_for_user(db: Session, user: User) -> Doctor:
    doctor = db.execute(
        select(Doctor)
        .options(selectinload(Doctor.specialty), selectinload(Doctor.hospital))
        .where(Doctor.user_id == user.id)
    ).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No doctor profile is linked to this account.",
        )
    return doctor


def doctor_has_patient(db: Session, doctor_user_id: str, patient_user_id: str) -> bool:
    """True when an appointment or referral links this doctor to the patient."""
    doctor = db.execute(
        select(Doctor).where(Doctor.user_id == doctor_user_id)
    ).scalar_one_or_none()
    if doctor is None:
        return False

    linked = db.execute(
        select(Appointment.id)
        .where(
            Appointment.doctor_id == doctor.id,
            Appointment.patient_user_id == patient_user_id,
        )
        .limit(1)
    ).first()
    if linked:
        return True

    referred = db.execute(
        select(Referral.id)
        .where(
            Referral.to_doctor_id == doctor.id,
            Referral.patient_user_id == patient_user_id,
        )
        .limit(1)
    ).first()
    return referred is not None


# --------------------------------------------------------------------------
# Hospital scope
# --------------------------------------------------------------------------
def require_hospital_scope(current_user: User) -> str:
    """The hospital a hospital-admin caller is allowed to operate on."""
    if str(current_user.role) == str(UserRole.SYSTEM_ADMIN):
        return ""  # system admins are unscoped
    if not current_user.hospital_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not linked to a hospital.",
        )
    return current_user.hospital_id
