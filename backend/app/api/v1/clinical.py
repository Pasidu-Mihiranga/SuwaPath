"""Doctor workspace: live queue, pre-consultation summary and consultations.

The pre-consultation summary is assembled from structured intake, uploaded
reports, image screening and prior consultations — but always alongside the
patient's original answers, never replacing them (spec §10).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import doctor_has_patient, get_doctor_for_user, get_patient_profile
from app.core.db import get_db
from app.core.security import get_current_user, require_doctor
from app.models.care import Appointment, Consultation, Medication, Referral
from app.models.clinical import (
    ExtractedReport,
    ExtractedValue,
    ImageAnalysis,
    MedicalDocument,
    MedicalImage,
    Recommendation,
    RedFlagAssessment,
    StructuredIntake,
    SymptomSession,
)
from app.models.enums import (
    AppointmentStatus,
    ConsultationStatus,
    NotificationCategory,
    NotificationPriority,
    ReferralStatus,
    UrgencyLevel,
    UserRole,
)
from app.models.identity import User
from app.models.platform import Notification
from app.models.providers import Doctor, Specialty

router = APIRouter(prefix="/doctor", tags=["doctor"])

URGENCY_ORDER = {"emergency": 0, "urgent": 1, "routine": 2, "self_care": 3}


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, time.min, tzinfo=timezone.utc),
        datetime.combine(day, time.max, tzinfo=timezone.utc),
    )


# --------------------------------------------------------------------------
# Queue and dashboard
# --------------------------------------------------------------------------
@router.get("/queue")
def live_queue(
    day: date | None = None,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> dict:
    """Today's patient queue, urgent cases first."""
    doctor = get_doctor_for_user(db, current_user)
    target_day = day or datetime.now(timezone.utc).date()
    start, end = _day_bounds(target_day)

    appointments = db.execute(
        select(Appointment)
        .options(selectinload(Appointment.patient))
        .where(
            Appointment.doctor_id == doctor.id,
            Appointment.scheduled_start >= start,
            Appointment.scheduled_start <= end,
            Appointment.status != str(AppointmentStatus.CANCELLED),
        )
        .order_by(Appointment.scheduled_start)
    ).scalars().unique().all()

    entries = []
    for appointment in appointments:
        profile = get_patient_profile(db, appointment.patient_user_id)
        entries.append(
            {
                "appointment_id": appointment.id,
                "patient_user_id": appointment.patient_user_id,
                "patient_name": appointment.patient.full_name if appointment.patient else "Unknown",
                "age": profile.age if profile else None,
                "sex": str(profile.sex) if profile and profile.sex else None,
                "chief_complaint": appointment.chief_complaint or appointment.reason,
                "urgency": str(appointment.urgency),
                "status": str(appointment.status),
                "visit_type": str(appointment.visit_type),
                "scheduled_start": appointment.scheduled_start,
                "teleconsultation_url": appointment.teleconsultation_url,
            }
        )

    # Urgent first, then by scheduled time (internal rule 2 applied to the UI).
    entries.sort(
        key=lambda e: (URGENCY_ORDER.get(e["urgency"], 9), e["scheduled_start"])
    )

    counts = {
        "total": len(entries),
        "urgent": sum(1 for e in entries if e["urgency"] in ("emergency", "urgent")),
        "waiting": sum(
            1 for e in entries if e["status"] in ("confirmed", "pending", "checked_in")
        ),
        "in_consultation": sum(1 for e in entries if e["status"] == "in_consultation"),
        "completed": sum(1 for e in entries if e["status"] == "completed"),
    }
    return {"date": target_day.isoformat(), "counts": counts, "queue": entries}


@router.get("/dashboard")
def doctor_dashboard(
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> dict:
    doctor = get_doctor_for_user(db, current_user)
    today = datetime.now(timezone.utc).date()
    start, end = _day_bounds(today)

    today_appointments = db.execute(
        select(Appointment).where(
            Appointment.doctor_id == doctor.id,
            Appointment.scheduled_start >= start,
            Appointment.scheduled_start <= end,
            Appointment.status != str(AppointmentStatus.CANCELLED),
        )
    ).scalars().all()

    follow_ups = db.execute(
        select(Consultation).where(
            Consultation.doctor_id == doctor.id,
            Consultation.follow_up_required.is_(True),
            Consultation.follow_up_date <= today + timedelta(days=7),
        )
    ).scalars().all()

    # Reports uploaded by this doctor's patients that have not yet been seen in
    # a consultation with them.
    patient_ids = {a.patient_user_id for a in today_appointments}
    pending_reports = 0
    if patient_ids:
        pending_reports = len(
            db.execute(
                select(MedicalDocument.id).where(
                    MedicalDocument.patient_user_id.in_(patient_ids),
                    MedicalDocument.created_at >= datetime.now(timezone.utc) - timedelta(days=30),
                )
            ).scalars().all()
        )

    completed = sum(1 for a in today_appointments if str(a.status) == "completed")
    in_progress = sum(1 for a in today_appointments if str(a.status) == "in_consultation")

    return {
        "doctor": {
            "id": doctor.id,
            "name": current_user.full_name,
            "specialty_name": doctor.specialty.name if doctor.specialty else None,
            "hospital_name": doctor.hospital.name if doctor.hospital else None,
        },
        "todays_patients": len(today_appointments),
        "urgent_cases": sum(
            1 for a in today_appointments if str(a.urgency) in ("emergency", "urgent")
        ),
        "follow_ups_due": len(follow_ups),
        "reports_pending_review": pending_reports,
        "workload": {
            "completed": completed,
            "in_progress": in_progress,
            "remaining": len(today_appointments) - completed - in_progress,
            "percent_complete": (
                round(completed / len(today_appointments) * 100)
                if today_appointments
                else 0
            ),
        },
        "schedule": [
            {
                "appointment_id": a.id,
                "scheduled_start": a.scheduled_start,
                "status": str(a.status),
                "urgency": str(a.urgency),
                "visit_type": str(a.visit_type),
            }
            for a in sorted(today_appointments, key=lambda a: a.scheduled_start)
        ],
    }


# --------------------------------------------------------------------------
# Pre-consultation summary
# --------------------------------------------------------------------------
@router.get("/patients/{patient_user_id}/pre-consultation")
def pre_consultation_summary(
    patient_user_id: str,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> dict:
    """Structured summary a doctor receives before the consultation."""
    if not doctor_has_patient(db, current_user.id, patient_user_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "You can only open records for patients booked with you or "
                "referred to you."
            ),
        )

    patient = db.get(User, patient_user_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    profile = get_patient_profile(db, patient_user_id)
    doctor = get_doctor_for_user(db, current_user)

    # Most recent completed intake.
    intake = db.execute(
        select(StructuredIntake)
        .options(selectinload(StructuredIntake.red_flag_assessment))
        .where(StructuredIntake.patient_user_id == patient_user_id)
        .order_by(StructuredIntake.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    session_messages = []
    if intake:
        session = db.execute(
            select(SymptomSession)
            .options(selectinload(SymptomSession.messages))
            .where(SymptomSession.id == intake.session_id)
        ).scalar_one_or_none()
        if session:
            # The doctor must retain access to the original patient answers.
            session_messages = [
                {"role": m.role, "content": m.content, "sequence": m.sequence}
                for m in sorted(session.messages, key=lambda m: m.sequence)
            ]

    recommendation = db.execute(
        select(Recommendation)
        .where(Recommendation.patient_user_id == patient_user_id)
        .order_by(Recommendation.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    documents = db.execute(
        select(MedicalDocument)
        .options(selectinload(MedicalDocument.extracted))
        .where(MedicalDocument.patient_user_id == patient_user_id)
        .order_by(MedicalDocument.created_at.desc())
        .limit(8)
    ).scalars().unique().all()

    images = db.execute(
        select(MedicalImage)
        .options(selectinload(MedicalImage.analysis))
        .where(MedicalImage.patient_user_id == patient_user_id)
        .order_by(MedicalImage.created_at.desc())
        .limit(5)
    ).scalars().unique().all()

    previous = db.execute(
        select(Consultation)
        .options(selectinload(Consultation.doctor).selectinload(Doctor.specialty))
        .where(
            Consultation.patient_user_id == patient_user_id,
            Consultation.status == str(ConsultationStatus.COMPLETED),
        )
        .order_by(Consultation.ended_at.desc().nullslast())
        .limit(5)
    ).scalars().unique().all()

    medications = db.execute(
        select(Medication).where(
            Medication.patient_user_id == patient_user_id,
            Medication.is_active.is_(True),
        )
    ).scalars().all()

    assessment = intake.red_flag_assessment if intake else None

    return {
        "patient": {
            "user_id": patient.id,
            "name": patient.full_name,
            "age": profile.age if profile else None,
            "sex": str(profile.sex) if profile and profile.sex else None,
            "blood_group": profile.blood_group if profile else None,
            "city": profile.city if profile else None,
            "preferred_language": str(patient.preferred_language),
            "is_pregnant": profile.is_pregnant if profile else False,
        },
        # AI-derived structure, clearly labelled as such.
        "structured_intake": (
            {
                "chief_complaint": intake.chief_complaint,
                "symptoms": intake.symptoms,
                "duration": intake.duration_text,
                "severity": intake.severity,
                "associated_symptoms": intake.associated_symptoms,
                "relevant_history": intake.relevant_history,
                "medications": intake.medications,
                "allergies": intake.allergies,
                "negative_findings": intake.negative_findings,
                "extraction_source": intake.extraction_source,
                "extraction_confidence": intake.extraction_confidence,
                "recorded_at": intake.created_at,
            }
            if intake
            else None
        ),
        # Original patient answers, never overwritten by the summary.
        "original_conversation": session_messages,
        "red_flags": (
            {
                "urgency": str(assessment.urgency),
                "triggered_rules": assessment.triggered_rules,
                "escalation_message": assessment.escalation_message,
                "rule_engine_version": assessment.rule_engine_version,
            }
            if assessment
            else {"urgency": None, "triggered_rules": [], "escalation_message": None}
        ),
        "profile_history": {
            "chronic_conditions": profile.chronic_conditions if profile else [],
            "allergies": profile.allergies if profile else [],
            "past_surgeries": profile.past_surgeries if profile else [],
            "family_history": profile.family_history if profile else [],
        },
        "current_medications": [
            {
                "name": m.name,
                "dosage": m.dosage,
                "frequency": m.frequency_label,
                "is_critical": m.is_critical,
            }
            for m in medications
        ],
        "reports": [
            {
                "id": d.id,
                "file_name": d.file_name,
                "document_type": str(d.document_type),
                "uploaded_at": d.created_at,
                "summary": d.extracted.summary if d.extracted else None,
                "key_findings": d.extracted.key_findings if d.extracted else [],
            }
            for d in documents
        ],
        "image_screening": [
            {
                "id": i.id,
                "modality": str(i.modality),
                "uploaded_at": i.created_at,
                "finding_label": i.analysis.finding_label if i.analysis else None,
                "confidence": i.analysis.confidence if i.analysis else None,
                "is_uncertain": i.analysis.is_uncertain if i.analysis else None,
                "model_name": i.analysis.model_name if i.analysis else None,
                "has_visual_explanation": (
                    i.analysis.has_visual_explanation if i.analysis else False
                ),
            }
            for i in images
        ],
        "previous_consultations": [
            {
                "id": c.id,
                "date": c.ended_at or c.started_at,
                "specialty": c.doctor.specialty.name if c.doctor and c.doctor.specialty else None,
                "presenting_complaint": c.presenting_complaint,
                "assessment": c.assessment,
                "treatment_plan": c.treatment_plan,
                "follow_up_required": c.follow_up_required,
            }
            for c in previous
        ],
        "ai_recommendation": (
            {
                "specialty_code": recommendation.specialty_code,
                "specialty_name": (
                    recommendation.specialty.name if recommendation.specialty else None
                ),
                "urgency": str(recommendation.urgency),
                "reason": recommendation.reason,
                "confidence": recommendation.confidence,
                "recommended_tests": recommendation.recommended_tests,
                "source": recommendation.source,
            }
            if recommendation
            else None
        ),
        "viewing_doctor": {
            "id": doctor.id,
            "specialty_name": doctor.specialty.name if doctor.specialty else None,
        },
    }


# --------------------------------------------------------------------------
# Consultations
# --------------------------------------------------------------------------
class StartConsultationRequest(BaseModel):
    appointment_id: str


class ConsultationNotesRequest(BaseModel):
    clinical_notes: str | None = None
    assessment: str | None = None
    diagnosis_text: str | None = None
    treatment_plan: str | None = None
    prescribed_medications: list[dict] | None = None
    requested_tests: list[str] | None = None
    follow_up_required: bool | None = None
    follow_up_date: date | None = None
    follow_up_notes: str | None = None
    ai_specialty_accepted: bool | None = None


def _consultation_dict(consultation: Consultation) -> dict:
    return {
        "id": consultation.id,
        "appointment_id": consultation.appointment_id,
        "patient_user_id": consultation.patient_user_id,
        "status": str(consultation.status),
        "started_at": consultation.started_at,
        "ended_at": consultation.ended_at,
        "presenting_complaint": consultation.presenting_complaint,
        "clinical_notes": consultation.clinical_notes,
        "assessment": consultation.assessment,
        "diagnosis_text": consultation.diagnosis_text,
        "treatment_plan": consultation.treatment_plan,
        "prescribed_medications": consultation.prescribed_medications,
        "requested_tests": consultation.requested_tests,
        "follow_up_required": consultation.follow_up_required,
        "follow_up_date": consultation.follow_up_date,
        "follow_up_notes": consultation.follow_up_notes,
        "ai_specialty_accepted": consultation.ai_specialty_accepted,
        "duration_minutes": consultation.duration_minutes,
    }


@router.post("/consultations", status_code=status.HTTP_201_CREATED)
def start_consultation(
    payload: StartConsultationRequest,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> dict:
    doctor = get_doctor_for_user(db, current_user)
    appointment = db.get(Appointment, payload.appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    if appointment.doctor_id != doctor.id:
        raise HTTPException(
            status_code=403, detail="This appointment belongs to another doctor."
        )

    existing = db.execute(
        select(Consultation).where(Consultation.appointment_id == appointment.id)
    ).scalar_one_or_none()
    if existing:
        return _consultation_dict(existing)

    now = datetime.now(timezone.utc)
    consultation = Consultation(
        appointment_id=appointment.id,
        patient_user_id=appointment.patient_user_id,
        doctor_id=doctor.id,
        hospital_id=appointment.hospital_id,
        status=ConsultationStatus.IN_PROGRESS,
        started_at=now,
        presenting_complaint=appointment.chief_complaint or appointment.reason,
    )
    db.add(consultation)

    appointment.status = AppointmentStatus.IN_CONSULTATION
    appointment.started_at = now
    db.commit()
    db.refresh(consultation)
    return _consultation_dict(consultation)


@router.patch("/consultations/{consultation_id}")
def update_consultation(
    consultation_id: str,
    payload: ConsultationNotesRequest,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> dict:
    doctor = get_doctor_for_user(db, current_user)
    consultation = db.get(Consultation, consultation_id)
    if consultation is None:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    if consultation.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Not your consultation.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(consultation, field, value)

    db.commit()
    db.refresh(consultation)
    return _consultation_dict(consultation)


@router.post("/consultations/{consultation_id}/complete")
def complete_consultation(
    consultation_id: str,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> dict:
    doctor = get_doctor_for_user(db, current_user)
    consultation = db.get(Consultation, consultation_id)
    if consultation is None:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    if consultation.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Not your consultation.")

    now = datetime.now(timezone.utc)
    consultation.status = ConsultationStatus.COMPLETED
    consultation.ended_at = now
    if consultation.started_at:
        consultation.duration_minutes = max(
            1, int((now - consultation.started_at).total_seconds() / 60)
        )

    if consultation.appointment_id:
        appointment = db.get(Appointment, consultation.appointment_id)
        if appointment:
            appointment.status = AppointmentStatus.COMPLETED
            appointment.completed_at = now

    doctor.total_consultations += 1

    if consultation.follow_up_required and consultation.follow_up_date:
        db.add(
            Notification(
                user_id=consultation.patient_user_id,
                category=NotificationCategory.FOLLOW_UP,
                priority=NotificationPriority.NORMAL,
                title="Follow-up recommended",
                body=(
                    f"{current_user.full_name} recommended a follow-up on "
                    f"{consultation.follow_up_date.strftime('%d %b %Y')}."
                    + (f" {consultation.follow_up_notes}" if consultation.follow_up_notes else "")
                ),
                action_type="consultation",
                action_id=consultation.id,
            )
        )
    if consultation.requested_tests:
        db.add(
            Notification(
                user_id=consultation.patient_user_id,
                category=NotificationCategory.FOLLOW_UP,
                priority=NotificationPriority.NORMAL,
                title="Tests requested",
                body=(
                    "Your doctor requested: "
                    + ", ".join(consultation.requested_tests[:5])
                ),
                action_type="consultation",
                action_id=consultation.id,
            )
        )

    db.commit()
    db.refresh(consultation)
    return _consultation_dict(consultation)


class ReferralRequest(BaseModel):
    patient_user_id: str
    specialty_code: str
    reason: str = Field(max_length=1000)
    to_doctor_id: str | None = None
    to_hospital_id: str | None = None
    urgency: UrgencyLevel = UrgencyLevel.ROUTINE
    consultation_id: str | None = None


@router.post("/referrals", status_code=status.HTTP_201_CREATED)
def create_referral(
    payload: ReferralRequest,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> dict:
    doctor = get_doctor_for_user(db, current_user)
    if not doctor_has_patient(db, current_user.id, payload.patient_user_id):
        raise HTTPException(
            status_code=403, detail="You do not have a care relationship with this patient."
        )

    referral = Referral(
        patient_user_id=payload.patient_user_id,
        from_doctor_id=doctor.id,
        to_doctor_id=payload.to_doctor_id,
        to_hospital_id=payload.to_hospital_id,
        consultation_id=payload.consultation_id,
        specialty_code=payload.specialty_code,
        reason=payload.reason,
        urgency=payload.urgency,
        status=ReferralStatus.PENDING,
        due_date=(datetime.now(timezone.utc) + timedelta(days=30)).date(),
    )
    db.add(referral)
    db.add(
        Notification(
            user_id=payload.patient_user_id,
            category=NotificationCategory.FOLLOW_UP,
            priority=NotificationPriority.HIGH,
            title="You have been referred",
            body=(
                f"{current_user.full_name} referred you to "
                f"{payload.specialty_code.replace('_', ' ')}. Reason: {payload.reason}"
            ),
            action_type="referral",
        )
    )
    db.commit()
    db.refresh(referral)
    return {
        "id": referral.id,
        "specialty_code": referral.specialty_code,
        "reason": referral.reason,
        "urgency": str(referral.urgency),
        "status": str(referral.status),
        "due_date": referral.due_date,
    }


@router.get("/patients")
def my_patients(
    limit: int = Query(default=50, le=200),
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Patients this doctor has a care relationship with (rule 8)."""
    doctor = get_doctor_for_user(db, current_user)

    rows = db.execute(
        select(Appointment.patient_user_id, Appointment.scheduled_start)
        .where(Appointment.doctor_id == doctor.id)
        .order_by(Appointment.scheduled_start.desc())
        .limit(limit * 4)
    ).all()

    seen: dict[str, datetime] = {}
    for patient_id, start in rows:
        seen.setdefault(patient_id, start)

    out = []
    for patient_id, last_seen in list(seen.items())[:limit]:
        patient = db.get(User, patient_id)
        profile = get_patient_profile(db, patient_id)
        if patient is None:
            continue
        out.append(
            {
                "user_id": patient_id,
                "name": patient.full_name,
                "age": profile.age if profile else None,
                "sex": str(profile.sex) if profile and profile.sex else None,
                "last_appointment": last_seen,
            }
        )
    return out
