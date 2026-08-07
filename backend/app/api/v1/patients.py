"""Patient dashboard, medical history and in-app notifications."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_patient_profile, resolve_patient_access
from app.core.db import get_db
from app.core.security import get_current_user, require_patient
from app.models.care import (
    Appointment,
    CareEnrollment,
    Consultation,
    MaternalRecord,
    Medication,
    MedicationLog,
)
from app.models.clinical import (
    MedicalDocument,
    MedicalImage,
    Recommendation,
    StructuredIntake,
)
from app.models.enums import (
    AppointmentStatus,
    GuardianPermissionType,
    MedicationLogStatus,
    NotificationCategory,
    UserRole,
)
from app.models.identity import User
from app.models.platform import Notification
from app.models.providers import Doctor

router = APIRouter(tags=["patients"])


@router.get("/patients/me/dashboard")
def dashboard(
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> dict:
    """Everything the patient home screen needs, in one call."""
    now = datetime.now(timezone.utc)
    profile = get_patient_profile(db, current_user.id)

    upcoming = db.execute(
        select(Appointment)
        .options(
            selectinload(Appointment.doctor).selectinload(Doctor.user),
            selectinload(Appointment.doctor).selectinload(Doctor.specialty),
            selectinload(Appointment.hospital),
        )
        .where(
            Appointment.patient_user_id == current_user.id,
            Appointment.scheduled_start >= now,
            Appointment.status.notin_(
                [str(AppointmentStatus.CANCELLED), str(AppointmentStatus.NO_SHOW)]
            ),
        )
        .order_by(Appointment.scheduled_start)
        .limit(3)
    ).scalars().unique().all()

    recommendation = db.execute(
        select(Recommendation)
        .where(
            Recommendation.patient_user_id == current_user.id,
            Recommendation.is_active.is_(True),
        )
        .order_by(Recommendation.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    recent_document = db.execute(
        select(MedicalDocument)
        .options(selectinload(MedicalDocument.extracted))
        .where(MedicalDocument.patient_user_id == current_user.id)
        .order_by(MedicalDocument.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    recent_image = db.execute(
        select(MedicalImage)
        .options(selectinload(MedicalImage.analysis))
        .where(MedicalImage.patient_user_id == current_user.id)
        .order_by(MedicalImage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    enrollments = db.execute(
        select(CareEnrollment)
        .options(selectinload(CareEnrollment.programme))
        .where(
            CareEnrollment.patient_user_id == current_user.id,
            CareEnrollment.status == "active",
        )
    ).scalars().unique().all()

    maternal = db.execute(
        select(MaternalRecord).where(MaternalRecord.patient_user_id == current_user.id)
    ).scalar_one_or_none()

    medications = db.execute(
        select(Medication).where(
            Medication.patient_user_id == current_user.id,
            Medication.is_active.is_(True),
        )
    ).scalars().all()

    # Doses due in the next 12 hours or overdue today.
    reminders = []
    for medication in medications[:6]:
        latest = db.execute(
            select(MedicationLog)
            .where(
                MedicationLog.medication_id == medication.id,
                MedicationLog.due_at <= now,
            )
            .order_by(MedicationLog.due_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        reminders.append(
            {
                "medication_id": medication.id,
                "name": medication.name,
                "dosage": medication.dosage,
                "frequency_label": medication.frequency_label,
                "schedule_times": medication.schedule_times,
                "is_critical": medication.is_critical,
                "last_status": str(latest.status) if latest else None,
            }
        )

    unread = len(
        db.execute(
            select(Notification.id).where(
                Notification.user_id == current_user.id,
                Notification.is_read.is_(False),
                or_(
                    Notification.scheduled_for.is_(None),
                    Notification.scheduled_for <= now,
                ),
            )
        ).scalars().all()
    )

    activity = _recent_activity(db, current_user.id)

    return {
        "patient": {
            "name": current_user.full_name,
            "age": profile.age if profile else None,
            "city": profile.city if profile else None,
            "preferred_language": str(current_user.preferred_language),
            "accessibility_large_text": (
                profile.accessibility_large_text if profile else False
            ),
        },
        "upcoming_appointments": [
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
                "reason": a.reason,
                "teleconsultation_url": a.teleconsultation_url,
            }
            for a in upcoming
        ],
        "current_recommendation": (
            {
                "id": recommendation.id,
                "specialty_code": recommendation.specialty_code,
                "specialty_name": (
                    recommendation.specialty.name if recommendation.specialty else None
                ),
                "urgency": str(recommendation.urgency),
                "reason": recommendation.reason,
                "suggested_next_action": recommendation.suggested_next_action,
                "confidence": recommendation.confidence,
                "source": recommendation.source,
                "created_at": recommendation.created_at,
            }
            if recommendation
            else None
        ),
        "recent_report": (
            {
                "id": recent_document.id,
                "file_name": recent_document.file_name,
                "uploaded_at": recent_document.created_at,
                "summary": (
                    recent_document.extracted.summary
                    if recent_document.extracted
                    else None
                ),
                "status": str(recent_document.processing_status),
            }
            if recent_document
            else None
        ),
        "recent_screening": (
            {
                "id": recent_image.id,
                "modality": str(recent_image.modality),
                "uploaded_at": recent_image.created_at,
                "finding_label": (
                    recent_image.analysis.finding_label if recent_image.analysis else None
                ),
                "confidence": (
                    recent_image.analysis.confidence if recent_image.analysis else None
                ),
                "is_uncertain": (
                    recent_image.analysis.is_uncertain if recent_image.analysis else None
                ),
            }
            if recent_image
            else None
        ),
        "care_programmes": [
            {
                "id": e.id,
                "code": e.programme.code,
                "name": e.programme.name,
                "type": str(e.programme.programme_type),
                "progress_percent": e.progress_percent,
            }
            for e in enrollments
        ],
        "maternal_summary": (
            {
                "pregnancy_week": maternal.pregnancy_week,
                "expected_delivery_date": maternal.expected_delivery_date,
                "is_postpartum": maternal.is_postpartum,
                "is_high_risk": maternal.is_high_risk,
            }
            if maternal
            else None
        ),
        "medication_reminders": reminders,
        "unread_notifications": unread,
        "recent_activity": activity,
    }


def _recent_activity(db: Session, user_id: str) -> list[dict]:
    """Merged, time-ordered feed across the patient's records."""
    items: list[dict] = []

    for document in db.execute(
        select(MedicalDocument)
        .where(MedicalDocument.patient_user_id == user_id)
        .order_by(MedicalDocument.created_at.desc())
        .limit(5)
    ).scalars():
        items.append(
            {
                "type": "report_uploaded",
                "title": f"Report uploaded: {document.file_name}",
                "at": document.created_at,
                "action_id": document.id,
            }
        )

    for intake in db.execute(
        select(StructuredIntake)
        .where(StructuredIntake.patient_user_id == user_id)
        .order_by(StructuredIntake.created_at.desc())
        .limit(5)
    ).scalars():
        items.append(
            {
                "type": "symptom_check",
                "title": f"Symptom check completed: {intake.chief_complaint}",
                "at": intake.created_at,
                "action_id": intake.session_id,
            }
        )

    for appointment in db.execute(
        select(Appointment)
        .where(Appointment.patient_user_id == user_id)
        .order_by(Appointment.created_at.desc())
        .limit(5)
    ).scalars():
        items.append(
            {
                "type": "appointment_booked",
                "title": "Appointment booked",
                "at": appointment.created_at,
                "action_id": appointment.id,
            }
        )

    for image in db.execute(
        select(MedicalImage)
        .where(MedicalImage.patient_user_id == user_id)
        .order_by(MedicalImage.created_at.desc())
        .limit(3)
    ).scalars():
        items.append(
            {
                "type": "image_screened",
                "title": "Medical image screened",
                "at": image.created_at,
                "action_id": image.id,
            }
        )

    items.sort(key=lambda i: i["at"], reverse=True)
    return items[:10]


@router.get("/patients/me/history")
def medical_history(
    patient_user_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Consultations, intakes, reports and screenings in one timeline."""
    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.FULL_MEDICAL
        )

    consultations = db.execute(
        select(Consultation)
        .options(
            selectinload(Consultation.doctor).selectinload(Doctor.user),
            selectinload(Consultation.doctor).selectinload(Doctor.specialty),
        )
        .where(Consultation.patient_user_id == target_id)
        .order_by(Consultation.started_at.desc().nullslast())
        .limit(30)
    ).scalars().unique().all()

    intakes = db.execute(
        select(StructuredIntake)
        .options(selectinload(StructuredIntake.red_flag_assessment))
        .where(StructuredIntake.patient_user_id == target_id)
        .order_by(StructuredIntake.created_at.desc())
        .limit(20)
    ).scalars().unique().all()

    return {
        "consultations": [
            {
                "id": c.id,
                "date": c.ended_at or c.started_at,
                "doctor_name": c.doctor.user.full_name if c.doctor and c.doctor.user else None,
                "specialty_name": (
                    c.doctor.specialty.name if c.doctor and c.doctor.specialty else None
                ),
                "presenting_complaint": c.presenting_complaint,
                "assessment": c.assessment,
                "treatment_plan": c.treatment_plan,
                "prescribed_medications": c.prescribed_medications,
                "requested_tests": c.requested_tests,
                "follow_up_required": c.follow_up_required,
                "follow_up_date": c.follow_up_date,
                "status": str(c.status),
            }
            for c in consultations
        ],
        "symptom_checks": [
            {
                "id": i.id,
                "session_id": i.session_id,
                "date": i.created_at,
                "chief_complaint": i.chief_complaint,
                "symptoms": i.symptoms,
                "urgency": (
                    str(i.red_flag_assessment.urgency) if i.red_flag_assessment else None
                ),
            }
            for i in intakes
        ],
    }


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------
@router.get("/notifications")
def list_notifications(
    unread_only: bool = False,
    category: NotificationCategory | None = None,
    limit: int = Query(default=50, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    now = datetime.now(timezone.utc)
    stmt = select(Notification).where(
        Notification.user_id == current_user.id,
        # Scheduled reminders stay hidden until they fall due.
        or_(Notification.scheduled_for.is_(None), Notification.scheduled_for <= now),
    )
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    if category:
        stmt = stmt.where(Notification.category == str(category))

    rows = db.execute(
        stmt.order_by(Notification.created_at.desc()).limit(limit)
    ).scalars().all()

    unread = len(
        db.execute(
            select(Notification.id).where(
                Notification.user_id == current_user.id,
                Notification.is_read.is_(False),
                or_(
                    Notification.scheduled_for.is_(None),
                    Notification.scheduled_for <= now,
                ),
            )
        ).scalars().all()
    )

    return {
        "unread_count": unread,
        "notifications": [
            {
                "id": n.id,
                "category": str(n.category),
                "priority": str(n.priority),
                "title": n.title,
                "body": n.body,
                "action_type": n.action_type,
                "action_id": n.action_id,
                "about_patient_user_id": n.about_patient_user_id,
                "is_read": n.is_read,
                "created_at": n.created_at,
            }
            for n in rows
        ],
    }


@router.post("/notifications/{notification_id}/read")
def mark_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Notification not found.")
    notification.is_read = True
    notification.read_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": notification.id, "is_read": True}


@router.post("/notifications/read-all")
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.execute(
        select(Notification).where(
            Notification.user_id == current_user.id, Notification.is_read.is_(False)
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for notification in rows:
        notification.is_read = True
        notification.read_at = now
    db.commit()
    return {"marked_read": len(rows)}
