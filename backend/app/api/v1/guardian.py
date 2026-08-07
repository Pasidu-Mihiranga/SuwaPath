"""Guardian experience and patient-controlled consent.

Guardians see only what the dependent has explicitly granted. The consent
management endpoints live here too, but are callable only by the patient.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_patient_profile, get_relationship, require_permission
from app.core.db import get_db
from app.core.security import get_current_user, require_guardian, require_patient
from app.models.care import (
    Appointment,
    CareEnrollment,
    DailyCheckIn,
    ElderlyRecord,
    MaternalRecord,
    Medication,
    MedicationLog,
)
from app.models.enums import (
    AppointmentStatus,
    GuardianPermissionType,
    MedicationLogStatus,
    UserRole,
)
from app.models.identity import GuardianPermission, GuardianRelationship, User
from app.models.platform import GuardianAlert
from app.models.providers import Doctor

router = APIRouter(tags=["guardian"])


# ==========================================================================
# Guardian side
# ==========================================================================
@router.get("/guardian/dependents")
def list_dependents(
    current_user: User = Depends(require_guardian),
    db: Session = Depends(get_db),
) -> list[dict]:
    relationships = db.execute(
        select(GuardianRelationship)
        .options(
            selectinload(GuardianRelationship.permissions),
            selectinload(GuardianRelationship.patient),
        )
        .where(
            GuardianRelationship.guardian_user_id == current_user.id,
            GuardianRelationship.is_active.is_(True),
        )
    ).scalars().unique().all()

    out = []
    for relationship in relationships:
        patient = relationship.patient
        profile = get_patient_profile(db, patient.id)
        granted = {str(s) for s in relationship.granted_scopes()}

        enrollment = db.execute(
            select(CareEnrollment)
            .options(selectinload(CareEnrollment.programme))
            .where(
                CareEnrollment.patient_user_id == patient.id,
                CareEnrollment.status == "active",
            )
            .limit(1)
        ).scalar_one_or_none()

        unread_alerts = len(
            db.execute(
                select(GuardianAlert.id).where(
                    GuardianAlert.guardian_user_id == current_user.id,
                    GuardianAlert.patient_user_id == patient.id,
                    GuardianAlert.is_acknowledged.is_(False),
                )
            ).scalars().all()
        )

        status_label, status_tone = _dependent_status(db, patient.id, granted)

        out.append(
            {
                "patient_user_id": patient.id,
                "name": patient.full_name,
                "relationship": relationship.relationship_label,
                "age": profile.age if profile else None,
                "care_programme": enrollment.programme.name if enrollment else None,
                "care_programme_type": (
                    str(enrollment.programme.programme_type) if enrollment else None
                ),
                "status_label": status_label,
                "status_tone": status_tone,
                "unread_alerts": unread_alerts,
                "granted_permissions": sorted(granted),
                "can_book_appointments": relationship.can_book_appointments,
            }
        )
    return out


def _dependent_status(db: Session, patient_id: str, granted: set[str]) -> tuple[str, str]:
    """Short status for the dependent card, respecting consent scopes."""
    full = str(GuardianPermissionType.FULL_MEDICAL) in granted

    if str(GuardianPermissionType.WELLBEING) in granted or full:
        record = db.execute(
            select(ElderlyRecord).where(ElderlyRecord.patient_user_id == patient_id)
        ).scalar_one_or_none()
        if record and record.consecutive_missed_checkins >= 2:
            return (
                f"No check-in for {record.consecutive_missed_checkins} days",
                "attention",
            )
        latest = db.execute(
            select(DailyCheckIn)
            .where(DailyCheckIn.patient_user_id == patient_id)
            .order_by(DailyCheckIn.check_in_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest and latest.triggered_alert:
            return ("Warning sign reported", "urgent")
        if latest and latest.check_in_date == date.today():
            return ("Checked in today", "normal")

    if str(GuardianPermissionType.CARE_PROGRAMME) in granted or full:
        maternal = db.execute(
            select(MaternalRecord).where(MaternalRecord.patient_user_id == patient_id)
        ).scalar_one_or_none()
        if maternal and not maternal.is_postpartum and maternal.pregnancy_week:
            return (f"Pregnancy week {maternal.pregnancy_week}", "programme")
        if maternal and maternal.is_postpartum:
            return ("Postpartum care", "programme")

    return ("No recent updates", "normal")


@router.get("/guardian/dependents/{patient_user_id}")
def dependent_detail(
    patient_user_id: str,
    current_user: User = Depends(require_guardian),
    db: Session = Depends(get_db),
) -> dict:
    """Everything the guardian is permitted to see about one dependent."""
    relationship = get_relationship(db, current_user.id, patient_user_id)
    granted = relationship.granted_scopes()
    full = GuardianPermissionType.FULL_MEDICAL in granted

    def allowed(permission: GuardianPermissionType) -> bool:
        return full or permission in granted

    patient = relationship.patient
    profile = get_patient_profile(db, patient_user_id)

    payload: dict = {
        "patient_user_id": patient.id,
        "name": patient.full_name,
        "relationship": relationship.relationship_label,
        "age": profile.age if profile else None,
        "sex": str(profile.sex) if profile and profile.sex else None,
        "city": profile.city if profile else None,
        "granted_permissions": sorted(str(s) for s in granted),
        "can_book_appointments": relationship.can_book_appointments,
        # Sections the patient has not shared are reported explicitly rather
        # than silently omitted, so the boundary is visible in the UI.
        "withheld_sections": [],
    }

    if allowed(GuardianPermissionType.APPOINTMENTS):
        rows = db.execute(
            select(Appointment)
            .options(
                selectinload(Appointment.doctor).selectinload(Doctor.user),
                selectinload(Appointment.doctor).selectinload(Doctor.specialty),
                selectinload(Appointment.hospital),
            )
            .where(
                Appointment.patient_user_id == patient_user_id,
                Appointment.scheduled_start >= datetime.now(timezone.utc),
                Appointment.status.notin_(
                    [str(AppointmentStatus.CANCELLED), str(AppointmentStatus.NO_SHOW)]
                ),
            )
            .order_by(Appointment.scheduled_start)
            .limit(10)
        ).scalars().unique().all()
        payload["appointments"] = [
            {
                "id": a.id,
                "scheduled_start": a.scheduled_start,
                "status": str(a.status),
                "visit_type": str(a.visit_type),
                "doctor_name": a.doctor.user.full_name if a.doctor and a.doctor.user else None,
                "specialty_name": (
                    a.doctor.specialty.name if a.doctor and a.doctor.specialty else None
                ),
                "hospital_name": a.hospital.name if a.hospital else None,
            }
            for a in rows
        ]
    else:
        payload["withheld_sections"].append("appointments")

    if allowed(GuardianPermissionType.MEDICATIONS):
        medications = db.execute(
            select(Medication).where(
                Medication.patient_user_id == patient_user_id,
                Medication.is_active.is_(True),
            )
        ).scalars().all()
        since = datetime.now(timezone.utc) - timedelta(days=14)
        entries = []
        for medication in medications:
            logs = db.execute(
                select(MedicationLog)
                .where(
                    MedicationLog.medication_id == medication.id,
                    MedicationLog.due_at >= since,
                )
                .order_by(MedicationLog.due_at.desc())
            ).scalars().all()
            taken = sum(
                1 for l in logs if str(l.status) == str(MedicationLogStatus.TAKEN)
            )
            consecutive = 0
            for log in logs:
                if str(log.status) == str(MedicationLogStatus.MISSED):
                    consecutive += 1
                else:
                    break
            entries.append(
                {
                    "id": medication.id,
                    "name": medication.name,
                    "dosage": medication.dosage,
                    "frequency_label": medication.frequency_label,
                    "is_critical": medication.is_critical,
                    "adherence_percent_14d": (
                        round(taken / len(logs) * 100) if logs else None
                    ),
                    "consecutive_missed": consecutive,
                }
            )
        payload["medications"] = entries
    else:
        payload["withheld_sections"].append("medications")

    if allowed(GuardianPermissionType.WELLBEING):
        check_ins = db.execute(
            select(DailyCheckIn)
            .where(DailyCheckIn.patient_user_id == patient_user_id)
            .order_by(DailyCheckIn.check_in_date.desc())
            .limit(14)
        ).scalars().all()
        payload["check_ins"] = [
            {
                "date": c.check_in_date,
                "wellbeing": str(c.wellbeing) if c.wellbeing else None,
                "danger_signs": c.danger_signs_reported,
                "triggered_alert": c.triggered_alert,
            }
            for c in check_ins
        ]
    else:
        payload["withheld_sections"].append("wellbeing")

    if allowed(GuardianPermissionType.CARE_PROGRAMME):
        enrollments = db.execute(
            select(CareEnrollment)
            .options(selectinload(CareEnrollment.programme))
            .where(
                CareEnrollment.patient_user_id == patient_user_id,
                CareEnrollment.status == "active",
            )
        ).scalars().unique().all()
        payload["care_programmes"] = [
            {
                "name": e.programme.name,
                "type": str(e.programme.programme_type),
                "progress_percent": e.progress_percent,
            }
            for e in enrollments
        ]
        maternal = db.execute(
            select(MaternalRecord).where(
                MaternalRecord.patient_user_id == patient_user_id
            )
        ).scalar_one_or_none()
        if maternal:
            payload["maternal"] = {
                "pregnancy_week": maternal.pregnancy_week,
                "expected_delivery_date": maternal.expected_delivery_date,
                "is_postpartum": maternal.is_postpartum,
                "is_high_risk": maternal.is_high_risk,
            }
    else:
        payload["withheld_sections"].append("care_programme")

    if allowed(GuardianPermissionType.REPORTS):
        from app.models.clinical import MedicalDocument

        documents = db.execute(
            select(MedicalDocument)
            .options(selectinload(MedicalDocument.extracted))
            .where(MedicalDocument.patient_user_id == patient_user_id)
            .order_by(MedicalDocument.created_at.desc())
            .limit(5)
        ).scalars().unique().all()
        payload["reports"] = [
            {
                "id": d.id,
                "file_name": d.file_name,
                "uploaded_at": d.created_at,
                "summary": d.extracted.summary if d.extracted else None,
            }
            for d in documents
        ]
    else:
        payload["withheld_sections"].append("reports")

    return payload


@router.get("/guardian/alerts")
def guardian_alerts(
    patient_user_id: str | None = None,
    only_unacknowledged: bool = False,
    limit: int = Query(default=50, le=200),
    current_user: User = Depends(require_guardian),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(GuardianAlert).where(
        GuardianAlert.guardian_user_id == current_user.id
    )
    if patient_user_id:
        get_relationship(db, current_user.id, patient_user_id)
        stmt = stmt.where(GuardianAlert.patient_user_id == patient_user_id)
    if only_unacknowledged:
        stmt = stmt.where(GuardianAlert.is_acknowledged.is_(False))

    alerts = db.execute(
        stmt.order_by(GuardianAlert.created_at.desc()).limit(limit)
    ).scalars().all()

    out = []
    for alert in alerts:
        patient = db.get(User, alert.patient_user_id)
        out.append(
            {
                "id": alert.id,
                "patient_user_id": alert.patient_user_id,
                "patient_name": patient.full_name if patient else None,
                "alert_type": alert.alert_type,
                "severity": str(alert.severity),
                "title": alert.title,
                "detail": alert.detail,
                "required_permission": alert.required_permission,
                "is_acknowledged": alert.is_acknowledged,
                "created_at": alert.created_at,
                "meta": alert.meta,
            }
        )
    return out


@router.post("/guardian/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str,
    current_user: User = Depends(require_guardian),
    db: Session = Depends(get_db),
) -> dict:
    alert = db.get(GuardianAlert, alert_id)
    if alert is None or alert.guardian_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Alert not found.")
    alert.is_acknowledged = True
    alert.acknowledged_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": alert.id, "is_acknowledged": True}


# ==========================================================================
# Patient side: consent management
# ==========================================================================
class InviteGuardianRequest(BaseModel):
    guardian_email: EmailStr
    relationship_label: str
    permissions: list[GuardianPermissionType] = []
    can_book_appointments: bool = False


@router.get("/patients/me/guardians")
def my_guardians(
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> list[dict]:
    relationships = db.execute(
        select(GuardianRelationship)
        .options(
            selectinload(GuardianRelationship.permissions),
            selectinload(GuardianRelationship.guardian),
        )
        .where(GuardianRelationship.patient_user_id == current_user.id)
    ).scalars().unique().all()

    return [
        {
            "relationship_id": r.id,
            "guardian_user_id": r.guardian_user_id,
            "guardian_name": r.guardian.full_name if r.guardian else None,
            "guardian_email": r.guardian.email if r.guardian else None,
            "relationship_label": r.relationship_label,
            "is_active": r.is_active,
            "can_book_appointments": r.can_book_appointments,
            "granted_permissions": sorted(str(s) for s in r.granted_scopes()),
            "all_permissions": [str(p) for p in GuardianPermissionType],
        }
        for r in relationships
    ]


@router.post("/patients/me/guardians", status_code=status.HTTP_201_CREATED)
def add_guardian(
    payload: InviteGuardianRequest,
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> dict:
    guardian = db.execute(
        select(User).where(User.email == payload.guardian_email.lower())
    ).scalar_one_or_none()
    if guardian is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No SuwaPath account found for that email. Ask them to register "
                "as a guardian first."
            ),
        )
    if guardian.id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot be your own guardian.")

    existing = db.execute(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_user_id == guardian.id,
            GuardianRelationship.patient_user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409, detail="This person is already one of your guardians."
        )

    relationship = GuardianRelationship(
        guardian_user_id=guardian.id,
        patient_user_id=current_user.id,
        relationship_label=payload.relationship_label,
        can_book_appointments=payload.can_book_appointments,
    )
    db.add(relationship)
    db.flush()

    now = datetime.now(timezone.utc)
    for permission in payload.permissions:
        db.add(
            GuardianPermission(
                relationship_id=relationship.id,
                permission=permission,
                granted=True,
                granted_at=now,
            )
        )
    db.commit()
    return {
        "relationship_id": relationship.id,
        "guardian_name": guardian.full_name,
        "granted_permissions": [str(p) for p in payload.permissions],
    }


class UpdatePermissionsRequest(BaseModel):
    permissions: list[GuardianPermissionType]
    can_book_appointments: bool | None = None


@router.put("/patients/me/guardians/{relationship_id}/permissions")
def update_permissions(
    relationship_id: str,
    payload: UpdatePermissionsRequest,
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> dict:
    relationship = db.execute(
        select(GuardianRelationship)
        .options(selectinload(GuardianRelationship.permissions))
        .where(GuardianRelationship.id == relationship_id)
    ).scalar_one_or_none()

    if relationship is None or relationship.patient_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Guardian relationship not found.")

    now = datetime.now(timezone.utc)
    requested = set(payload.permissions)
    existing = {p.permission: p for p in relationship.permissions}

    # Revoke anything no longer requested; grant or re-grant the rest.
    for permission, row in existing.items():
        if permission not in requested and row.granted:
            row.granted = False
            row.revoked_at = now

    for permission in requested:
        row = existing.get(permission)
        if row is None:
            db.add(
                GuardianPermission(
                    relationship_id=relationship.id,
                    permission=permission,
                    granted=True,
                    granted_at=now,
                )
            )
        elif not row.granted:
            row.granted = True
            row.granted_at = now
            row.revoked_at = None

    if payload.can_book_appointments is not None:
        relationship.can_book_appointments = payload.can_book_appointments

    db.commit()
    db.refresh(relationship)
    return {
        "relationship_id": relationship.id,
        "granted_permissions": sorted(str(s) for s in relationship.granted_scopes()),
        "can_book_appointments": relationship.can_book_appointments,
    }


@router.delete("/patients/me/guardians/{relationship_id}")
def revoke_guardian(
    relationship_id: str,
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> dict:
    relationship = db.get(GuardianRelationship, relationship_id)
    if relationship is None or relationship.patient_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Guardian relationship not found.")

    # Deactivated rather than deleted, so the audit trail survives.
    relationship.is_active = False
    relationship.can_book_appointments = False
    for permission in relationship.permissions:
        permission.granted = False
        permission.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return {"relationship_id": relationship_id, "is_active": False}
