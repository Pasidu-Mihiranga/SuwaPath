"""Care programmes: maternal/postpartum, elderly, medications, check-ins, vitals.

Danger-sign check-ins run through the same deterministic red-flag engine as the
symptom conversation, so a reported warning sign escalates identically wherever
it is entered.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import build_patient_context, resolve_patient_access
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.care import (
    CareEnrollment,
    CareProgramme,
    DailyCheckIn,
    ElderlyRecord,
    MaternalRecord,
    Medication,
    MedicationLog,
    VitalRecord,
)
from app.models.enums import (
    AlertSeverity,
    CheckInType,
    EnrollmentStatus,
    GuardianPermissionType,
    MedicationLogStatus,
    NotificationCategory,
    NotificationPriority,
    ProgrammeType,
    UrgencyLevel,
    UserRole,
    WellbeingStatus,
)
from app.models.identity import User
from app.models.platform import Notification
from app.services import timeslots
from app.services.detectors import medication as medication_detector
from app.services.red_flag_engine import assess_concepts, build_context

router = APIRouter(prefix="/care", tags=["care-programmes"])


# --------------------------------------------------------------------------
# Guardian alert helper (consent-aware)
# --------------------------------------------------------------------------
# Lives in services/alerts.py so background detectors can raise alerts without
# importing an API router. Re-exported here because the existing call sites in
# this module read better unqualified.
from app.services.alerts import raise_guardian_alerts  # noqa: E402


# --------------------------------------------------------------------------
# Programmes and enrolment
# --------------------------------------------------------------------------
@router.get("/programmes")
def list_programmes(db: Session = Depends(get_db)) -> list[dict]:
    programmes = db.execute(
        select(CareProgramme).where(CareProgramme.is_active.is_(True))
    ).scalars()
    return [
        {
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "programme_type": str(p.programme_type),
            "description": p.description,
            "milestones": p.milestones,
            "check_in_questions": p.check_in_questions,
        }
        for p in programmes
    ]


class EnrollRequest(BaseModel):
    programme_code: str
    expected_delivery_date: date | None = None
    last_menstrual_period: date | None = None


@router.post("/enrollments", status_code=status.HTTP_201_CREATED)
def enroll(
    payload: EnrollRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if str(current_user.role) != str(UserRole.PATIENT):
        raise HTTPException(status_code=403, detail="Only patients can enrol.")

    programme = db.execute(
        select(CareProgramme).where(CareProgramme.code == payload.programme_code)
    ).scalar_one_or_none()
    if programme is None:
        raise HTTPException(status_code=404, detail="Care programme not found.")

    # Enrolment is idempotent and self-healing. If an active enrolment already
    # exists we reuse it rather than returning 409, then fall through to the
    # record-provisioning block below. That matters because an enrolment whose
    # backing MaternalRecord/ElderlyRecord is missing would otherwise be
    # permanently stuck: the dashboard 404s and re-enrolling is refused.
    enrollment = db.execute(
        select(CareEnrollment).where(
            CareEnrollment.patient_user_id == current_user.id,
            CareEnrollment.programme_id == programme.id,
            CareEnrollment.status == str(EnrollmentStatus.ACTIVE),
        )
    ).scalar_one_or_none()

    already_enrolled = enrollment is not None
    if enrollment is None:
        enrollment = CareEnrollment(
            patient_user_id=current_user.id,
            programme_id=programme.id,
            status=EnrollmentStatus.ACTIVE,
            enrolled_at=datetime.now(timezone.utc),
        )
        db.add(enrollment)
        db.flush()

    if programme.programme_type in (ProgrammeType.MATERNAL, ProgrammeType.POSTPARTUM):
        record = db.execute(
            select(MaternalRecord).where(
                MaternalRecord.patient_user_id == current_user.id
            )
        ).scalar_one_or_none()
        if record is None:
            record = MaternalRecord(patient_user_id=current_user.id)
            db.add(record)
        record.enrollment_id = enrollment.id
        # Only overwrite dates the caller actually supplied, so a repeat call
        # (or a self-heal) never blanks an existing due date.
        if payload.expected_delivery_date is not None:
            record.expected_delivery_date = payload.expected_delivery_date
        if payload.last_menstrual_period is not None:
            record.last_menstrual_period = payload.last_menstrual_period
        record.is_postpartum = programme.programme_type == ProgrammeType.POSTPARTUM

    elif programme.programme_type == ProgrammeType.ELDERLY:
        record = db.execute(
            select(ElderlyRecord).where(ElderlyRecord.patient_user_id == current_user.id)
        ).scalar_one_or_none()
        if record is None:
            db.add(
                ElderlyRecord(
                    patient_user_id=current_user.id, enrollment_id=enrollment.id
                )
            )

    db.commit()
    return {
        "id": enrollment.id,
        "programme_code": programme.code,
        "programme_name": programme.name,
        "status": str(enrollment.status),
        "enrolled_at": enrollment.enrolled_at,
    }


@router.get("/enrollments")
def my_enrollments(
    patient_user_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.CARE_PROGRAMME
        )

    enrollments = db.execute(
        select(CareEnrollment)
        .options(selectinload(CareEnrollment.programme))
        .where(
            CareEnrollment.patient_user_id == target_id,
            CareEnrollment.status == str(EnrollmentStatus.ACTIVE),
        )
    ).scalars().unique()

    return [
        {
            "id": e.id,
            "programme_code": e.programme.code,
            "programme_name": e.programme.name,
            "programme_type": str(e.programme.programme_type),
            "status": str(e.status),
            "progress_percent": e.progress_percent,
            "enrolled_at": e.enrolled_at,
            "milestones": e.programme.milestones,
            "check_in_questions": e.programme.check_in_questions,
        }
        for e in enrollments
    ]


# --------------------------------------------------------------------------
# Maternal dashboard
# --------------------------------------------------------------------------
@router.get("/maternal")
def maternal_dashboard(
    patient_user_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.CARE_PROGRAMME
        )

    record = db.execute(
        select(MaternalRecord).where(MaternalRecord.patient_user_id == target_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=404, detail="No maternal care record for this patient."
        )

    recent_check_ins = db.execute(
        select(DailyCheckIn)
        .where(
            DailyCheckIn.patient_user_id == target_id,
            DailyCheckIn.check_in_type.in_(
                [str(CheckInType.MATERNAL), str(CheckInType.POSTPARTUM)]
            ),
        )
        .order_by(DailyCheckIn.check_in_date.desc())
        .limit(14)
    ).scalars().all()

    vitals = db.execute(
        select(VitalRecord)
        .where(
            VitalRecord.patient_user_id == target_id,
            VitalRecord.vital_type == "blood_pressure",
        )
        .order_by(VitalRecord.recorded_at.desc())
        .limit(12)
    ).scalars().all()

    week = record.pregnancy_week
    upcoming_milestone = None
    if week is not None:
        enrollment = db.get(CareEnrollment, record.enrollment_id) if record.enrollment_id else None
        milestones = enrollment.programme.milestones if enrollment and enrollment.programme else []
        upcoming = [m for m in milestones if m.get("week") and m["week"] >= week]
        upcoming_milestone = upcoming[0] if upcoming else None

    return {
        "is_postpartum": record.is_postpartum,
        "pregnancy_week": week,
        "expected_delivery_date": record.expected_delivery_date,
        "delivery_date": record.delivery_date,
        "delivery_type": record.delivery_type,
        "gravida": record.gravida,
        "para": record.para,
        "is_high_risk": record.is_high_risk,
        "risk_conditions": record.risk_conditions,
        "current_weight_kg": record.current_weight_kg,
        "baseline_weight_kg": record.baseline_weight_kg,
        "newborn": (
            {
                "name": record.newborn_name,
                "date_of_birth": record.newborn_dob,
                "birth_weight_kg": record.newborn_birth_weight_kg,
                "feeding_method": record.feeding_method,
            }
            if record.is_postpartum
            else None
        ),
        "epds_score": record.epds_score,
        "epds_interpretation": _epds_interpretation(record.epds_score),
        "upcoming_milestone": upcoming_milestone,
        "recent_check_ins": [
            {
                "date": c.check_in_date,
                "wellbeing": str(c.wellbeing) if c.wellbeing else None,
                "danger_signs": c.danger_signs_reported,
                "triggered_alert": c.triggered_alert,
            }
            for c in recent_check_ins
        ],
        "blood_pressure": [
            {
                "recorded_at": v.recorded_at,
                "systolic": v.systolic,
                "diastolic": v.diastolic,
                "is_abnormal": v.is_abnormal,
            }
            for v in vitals
        ],
    }


def _epds_interpretation(score: int | None) -> str | None:
    if score is None:
        return None
    if score >= 13:
        return (
            "Score suggests possible postnatal depression. A mental-health "
            "assessment is recommended."
        )
    if score >= 10:
        return "Score suggests some low mood. Discuss with your care team."
    return "Score is in the usual range."


# --------------------------------------------------------------------------
# Check-ins
# --------------------------------------------------------------------------
class CheckInRequest(BaseModel):
    check_in_type: CheckInType
    responses: dict[str, bool] = Field(default_factory=dict)
    wellbeing: WellbeingStatus | None = None
    notes: str | None = None


@router.post("/check-ins", status_code=status.HTTP_201_CREATED)
def submit_check_in(
    payload: CheckInRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Record a check-in and escalate any reported danger signs."""
    today = date.today()

    existing = db.execute(
        select(DailyCheckIn).where(
            DailyCheckIn.patient_user_id == current_user.id,
            DailyCheckIn.check_in_date == today,
            DailyCheckIn.check_in_type == str(payload.check_in_type),
        )
    ).scalar_one_or_none()

    # Reported danger-sign codes are lexicon concepts, so the same
    # deterministic engine that triages a conversation triages a check-in.
    flagged = [code for code, value in payload.responses.items() if value]
    context_data = build_patient_context(db, current_user.id)
    assessment = assess_concepts(
        set(flagged),
        build_context(
            age=context_data.get("age"),
            sex=context_data.get("sex"),
            is_pregnant=bool(context_data.get("is_pregnant")),
            is_postpartum=bool(context_data.get("is_postpartum")),
            chronic_conditions=context_data.get("chronic_conditions"),
        ),
    )

    needs_help = payload.wellbeing == WellbeingStatus.NEED_HELP
    triggered = bool(assessment.triggered_rules) or needs_help
    escalation = assessment.escalation_message if assessment.triggered_rules else None
    if needs_help and not escalation:
        escalation = (
            "You asked for help. Your care team and any authorised family "
            "contacts have been notified."
        )

    check_in = existing or DailyCheckIn(
        patient_user_id=current_user.id,
        check_in_type=payload.check_in_type,
        check_in_date=today,
    )
    check_in.responses = payload.responses
    check_in.wellbeing = payload.wellbeing
    check_in.danger_signs_reported = flagged
    check_in.triggered_alert = triggered
    check_in.escalation_message = escalation
    check_in.notes = payload.notes
    if existing is None:
        db.add(check_in)

    # Reset the elderly missed-check-in counter on a successful submission.
    if payload.check_in_type == CheckInType.ELDERLY:
        record = db.execute(
            select(ElderlyRecord).where(ElderlyRecord.patient_user_id == current_user.id)
        ).scalar_one_or_none()
        if record:
            record.last_check_in_at = datetime.now(timezone.utc)
            record.consecutive_missed_checkins = 0

    alerts_raised = 0
    if triggered:
        db.add(
            Notification(
                user_id=current_user.id,
                category=NotificationCategory.EMERGENCY
                if assessment.urgency == UrgencyLevel.EMERGENCY
                else NotificationCategory.CARE_PROGRAMME,
                priority=NotificationPriority.CRITICAL
                if assessment.urgency == UrgencyLevel.EMERGENCY
                else NotificationPriority.HIGH,
                title="Warning sign reported",
                body=escalation or "Please contact your care team.",
                action_type="check_in",
                action_id=check_in.id,
            )
        )
        alerts_raised = raise_guardian_alerts(
            db,
            patient=current_user,
            alert_type="danger_sign_reported",
            severity=(
                AlertSeverity.CRITICAL
                if assessment.urgency == UrgencyLevel.EMERGENCY
                else AlertSeverity.ATTENTION
            ),
            title=f"{current_user.full_name} reported a warning sign",
            detail=(
                (", ".join(f.replace('_', ' ') for f in flagged) or "Requested help")
                + ". " + (escalation or "")
            ),
            permission=GuardianPermissionType.EMERGENCY_ALERTS,
            meta={"danger_signs": flagged, "urgency": str(assessment.urgency)},
        )

    db.commit()
    return {
        "id": check_in.id,
        "check_in_date": check_in.check_in_date,
        "wellbeing": str(check_in.wellbeing) if check_in.wellbeing else None,
        "danger_signs_reported": flagged,
        "triggered_alert": triggered,
        "urgency": str(assessment.urgency),
        "escalation_message": escalation,
        "triggered_rules": assessment.rules_as_dicts(),
        "guardian_alerts_raised": alerts_raised,
    }


@router.get("/check-ins")
def list_check_ins(
    patient_user_id: str | None = None,
    limit: int = Query(default=30, le=120),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.WELLBEING
        )

    rows = db.execute(
        select(DailyCheckIn)
        .where(DailyCheckIn.patient_user_id == target_id)
        .order_by(DailyCheckIn.check_in_date.desc())
        .limit(limit)
    ).scalars()
    return [
        {
            "id": c.id,
            "check_in_type": str(c.check_in_type),
            "date": c.check_in_date,
            "wellbeing": str(c.wellbeing) if c.wellbeing else None,
            "responses": c.responses,
            "danger_signs_reported": c.danger_signs_reported,
            "triggered_alert": c.triggered_alert,
            "escalation_message": c.escalation_message,
        }
        for c in rows
    ]


# --------------------------------------------------------------------------
# Medications
# --------------------------------------------------------------------------
@router.get("/medications")
def list_medications(
    patient_user_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.MEDICATIONS
        )

    medications = db.execute(
        select(Medication).where(
            Medication.patient_user_id == target_id, Medication.is_active.is_(True)
        )
    ).scalars().all()

    since = datetime.now(timezone.utc) - timedelta(days=14)
    out = []
    for medication in medications:
        logs = db.execute(
            select(MedicationLog)
            .where(
                MedicationLog.medication_id == medication.id,
                MedicationLog.due_at >= since,
            )
            .order_by(MedicationLog.due_at.desc())
        ).scalars().all()

        taken = sum(1 for l in logs if str(l.status) == str(MedicationLogStatus.TAKEN))
        adherence = round(taken / len(logs) * 100) if logs else None

        # Consecutive misses ending at the most recent dose.
        consecutive_missed = 0
        for log in logs:
            if str(log.status) == str(MedicationLogStatus.MISSED):
                consecutive_missed += 1
            else:
                break

        out.append(
            {
                "id": medication.id,
                "name": medication.name,
                "dosage": medication.dosage,
                "form": medication.form,
                "instructions": medication.instructions,
                "frequency_label": medication.frequency_label,
                "schedule_times": medication.schedule_times,
                "is_critical": medication.is_critical,
                "start_date": medication.start_date,
                "adherence_percent_14d": adherence,
                "consecutive_missed": consecutive_missed,
                "next_due": _next_due(medication),
            }
        )
    return out


def _next_due(medication: Medication) -> datetime | None:
    """The next scheduled dose.

    Delegates to `timeslots` because a second reader now exists — the job that
    materialises overdue doses. This used to build times with `tzinfo=utc`,
    which made a schedule of "08:00" mean 13:30 in Colombo.
    """
    return timeslots.next_due(medication.schedule_times)


class MedicationLogRequest(BaseModel):
    medication_id: str
    status: MedicationLogStatus
    due_at: datetime | None = None
    snooze_minutes: int | None = None


@router.post("/medications/log", status_code=status.HTTP_201_CREATED)
def log_medication(
    payload: MedicationLogRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    medication = db.get(Medication, payload.medication_id)
    if medication is None:
        raise HTTPException(status_code=404, detail="Medication not found.")
    if medication.patient_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your medication.")

    now = datetime.now(timezone.utc)
    due_at = payload.due_at or now
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)

    log = db.execute(
        select(MedicationLog).where(
            MedicationLog.medication_id == medication.id,
            MedicationLog.due_at == due_at,
        )
    ).scalar_one_or_none()
    if log is None:
        log = MedicationLog(
            medication_id=medication.id,
            patient_user_id=current_user.id,
            due_at=due_at,
        )
        db.add(log)

    log.status = payload.status
    log.recorded_at = now
    if payload.status == MedicationLogStatus.SNOOZED:
        log.snoozed_until = now + timedelta(minutes=payload.snooze_minutes or 30)

    db.commit()
    return {
        "id": log.id,
        "medication_id": medication.id,
        "status": str(log.status),
        "due_at": log.due_at,
        "snoozed_until": log.snoozed_until,
    }


@router.post("/medications/detect-missed")
def detect_missed_medication(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Detect repeated missed doses and raise consent-scoped guardian alerts.

    Pattern-based on purpose: guardians are alerted on a *run* of missed doses,
    not on every single event (spec §14).

    The detection itself lives in `services/detectors/medication.py`, because
    the same check now runs on a schedule. Leaving a copy here would let the
    two definitions of "a run" drift apart, and the scheduled one is the copy
    that matters — a patient who has stopped taking their medication is not
    going to open the app and press this.
    """
    raised = medication_detector.detect_for_patient(db, current_user)
    record = db.execute(
        select(ElderlyRecord).where(ElderlyRecord.patient_user_id == current_user.id)
    ).scalar_one_or_none()
    return {
        "threshold": record.missed_medication_alert_threshold if record else 2,
        "alerts": raised,
    }


# --------------------------------------------------------------------------
# Vitals
# --------------------------------------------------------------------------
class VitalRequest(BaseModel):
    vital_type: str
    value_numeric: float | None = None
    systolic: int | None = None
    diastolic: int | None = None
    unit: str | None = None
    notes: str | None = None


@router.post("/vitals", status_code=status.HTTP_201_CREATED)
def record_vital(
    payload: VitalRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    abnormal = False
    if payload.vital_type == "blood_pressure" and payload.systolic and payload.diastolic:
        abnormal = payload.systolic >= 140 or payload.diastolic >= 90
    elif payload.vital_type == "blood_glucose" and payload.value_numeric:
        abnormal = payload.value_numeric >= 180 or payload.value_numeric <= 70

    vital = VitalRecord(
        patient_user_id=current_user.id,
        vital_type=payload.vital_type,
        value_numeric=payload.value_numeric,
        systolic=payload.systolic,
        diastolic=payload.diastolic,
        unit=payload.unit,
        recorded_at=datetime.now(timezone.utc),
        is_abnormal=abnormal,
        source="patient",
        notes=payload.notes,
    )
    db.add(vital)

    alerts = 0
    # Only markedly abnormal readings alert a guardian, to avoid alert fatigue.
    severe = (
        payload.vital_type == "blood_pressure"
        and payload.systolic
        and payload.systolic >= 160
    ) or (
        payload.vital_type == "blood_glucose"
        and payload.value_numeric
        and (payload.value_numeric >= 250 or payload.value_numeric <= 60)
    )
    if severe:
        reading = (
            f"{payload.systolic}/{payload.diastolic} mmHg"
            if payload.vital_type == "blood_pressure"
            else f"{payload.value_numeric} {payload.unit or ''}".strip()
        )
        alerts = raise_guardian_alerts(
            db,
            patient=current_user,
            alert_type="abnormal_vitals",
            severity=AlertSeverity.CRITICAL,
            title="Abnormal reading recorded",
            detail=f"{current_user.full_name} recorded {reading}, outside the usual range.",
            permission=GuardianPermissionType.WELLBEING,
            meta={"vital_type": payload.vital_type, "reading": reading},
        )

    db.commit()
    return {
        "id": vital.id,
        "vital_type": vital.vital_type,
        "recorded_at": vital.recorded_at,
        "is_abnormal": vital.is_abnormal,
        "guardian_alerts_raised": alerts,
    }


@router.get("/vitals")
def list_vitals(
    vital_type: str | None = None,
    patient_user_id: str | None = None,
    limit: int = Query(default=60, le=300),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.WELLBEING
        )

    stmt = select(VitalRecord).where(VitalRecord.patient_user_id == target_id)
    if vital_type:
        stmt = stmt.where(VitalRecord.vital_type == vital_type)

    rows = db.execute(
        stmt.order_by(VitalRecord.recorded_at.desc()).limit(limit)
    ).scalars()
    return [
        {
            "id": v.id,
            "vital_type": v.vital_type,
            "value_numeric": v.value_numeric,
            "systolic": v.systolic,
            "diastolic": v.diastolic,
            "unit": v.unit,
            "recorded_at": v.recorded_at,
            "is_abnormal": v.is_abnormal,
        }
        for v in rows
    ]


@router.get("/elderly")
def elderly_dashboard(
    patient_user_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.CARE_PROGRAMME
        )

    record = db.execute(
        select(ElderlyRecord).where(ElderlyRecord.patient_user_id == target_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="No elderly care record found.")

    today_check_in = db.execute(
        select(DailyCheckIn).where(
            DailyCheckIn.patient_user_id == target_id,
            DailyCheckIn.check_in_date == date.today(),
            DailyCheckIn.check_in_type == str(CheckInType.ELDERLY),
        )
    ).scalar_one_or_none()

    return {
        "living_situation": record.living_situation,
        "mobility_level": record.mobility_level,
        "uses_walking_aid": record.uses_walking_aid,
        "fall_risk_level": record.fall_risk_level,
        "chronic_conditions": record.chronic_conditions,
        "last_check_in_at": record.last_check_in_at,
        "consecutive_missed_checkins": record.consecutive_missed_checkins,
        "checked_in_today": today_check_in is not None,
        "todays_wellbeing": (
            str(today_check_in.wellbeing)
            if today_check_in and today_check_in.wellbeing
            else None
        ),
    }
