"""Agent tools over SuwaPath's real domain.

Privacy is enforced *here*, not in the prompt. Every tool takes the caller's
identity and consent scope and filters at the query, so an agent physically
cannot retrieve data the user is not entitled to — no amount of prompt
injection changes what comes back from the database.

Each tool returns ``(display_text, structured)``: the text is what may reach
the model, the structured half is what the UI renders. Identifiers live in the
structured half only, so a doctor's name shows in the results list without
ever entering the prompt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.clinical.catalog import SPECIALTY_BY_CODE
from app.models.care import Appointment, CareEnrollment, Medication
from app.models.clinical import (
    ExtractedReport,
    MedicalDocument,
    MedicalImage,
    Recommendation,
)
from app.models.enums import AppointmentStatus, GuardianPermissionType, UserRole
from app.models.identity import GuardianRelationship, PatientProfile, User
from app.models.providers import Doctor, Hospital
from app.services.knowledge import knowledge_service
from app.services.matching import MatchCriteria, match_doctors, match_facilities


class ToolDenied(PermissionError):
    """Raised when the caller's scope does not permit the requested data."""


# --------------------------------------------------------------------------
# Scope resolution
# --------------------------------------------------------------------------
def resolve_scope(db: Session, user: User, subject_user_id: str | None = None) -> dict:
    """Work out whose data this agent turn may touch, and how much of it.

    Returns the scope dict stored on ``AgentState``. It is consulted by every
    tool and is never included in a prompt.
    """
    role = str(user.role)
    subject = subject_user_id or user.id

    if role == str(UserRole.PATIENT):
        if subject != user.id:
            raise ToolDenied("You can only ask about your own records.")
        return {"subject_user_id": user.id, "role": role, "permissions": "self"}

    if role == str(UserRole.GUARDIAN):
        relationship = db.execute(
            select(GuardianRelationship)
            .options(selectinload(GuardianRelationship.permissions))
            .where(
                GuardianRelationship.guardian_user_id == user.id,
                GuardianRelationship.patient_user_id == subject,
                GuardianRelationship.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if relationship is None:
            raise ToolDenied("You are not a registered guardian for this person.")
        granted = {str(scope) for scope in relationship.granted_scopes()}
        return {
            "subject_user_id": subject,
            "role": role,
            "permissions": sorted(granted),
            "can_book": relationship.can_book_appointments,
        }

    if role == str(UserRole.DOCTOR):
        from app.api.deps import doctor_has_patient

        if subject != user.id and not doctor_has_patient(db, user.id, subject):
            raise ToolDenied(
                "You can only access patients booked with or referred to you."
            )
        return {"subject_user_id": subject, "role": role, "permissions": "clinical"}

    return {"subject_user_id": subject, "role": role, "permissions": "self"}


def _has(scope: dict, permission: GuardianPermissionType) -> bool:
    """Consent check. Patients and clinicians are not consent-gated."""
    permissions = scope.get("permissions")
    if permissions in ("self", "clinical"):
        return True
    if not isinstance(permissions, list):
        return False
    return (
        str(permission) in permissions
        or str(GuardianPermissionType.FULL_MEDICAL) in permissions
    )


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------
def tool_appointments(db: Session, scope: dict, **_: Any) -> tuple[str, dict]:
    """Upcoming appointments for the subject."""
    if not _has(scope, GuardianPermissionType.APPOINTMENTS):
        raise ToolDenied("This person has not shared their appointments with you.")

    rows = db.execute(
        select(Appointment)
        .options(
            selectinload(Appointment.doctor).selectinload(Doctor.user),
            selectinload(Appointment.doctor).selectinload(Doctor.specialty),
            selectinload(Appointment.hospital),
        )
        .where(
            Appointment.patient_user_id == scope["subject_user_id"],
            Appointment.scheduled_start >= datetime.now(timezone.utc),
            Appointment.status.notin_(
                [str(AppointmentStatus.CANCELLED), str(AppointmentStatus.NO_SHOW)]
            ),
        )
        .order_by(Appointment.scheduled_start)
        .limit(5)
    ).scalars().unique().all()

    if not rows:
        return "The patient has no upcoming appointments.", {"appointments": []}

    structured = [
        {
            "id": a.id,
            "when": a.scheduled_start.isoformat(),
            "doctor_name": a.doctor.user.full_name if a.doctor and a.doctor.user else None,
            "specialty": a.doctor.specialty.name if a.doctor and a.doctor.specialty else None,
            "hospital_name": a.hospital.name if a.hospital else None,
            "visit_type": str(a.visit_type),
            "status": str(a.status),
        }
        for a in rows
    ]
    # Names deliberately excluded from the model-visible text.
    lines = [
        f"- {a['specialty'] or 'Consultation'} on "
        f"{datetime.fromisoformat(a['when']).strftime('%d %b at %I:%M %p')} "
        f"({a['visit_type'].replace('_', ' ')}, {a['status']})"
        for a in structured
    ]
    return "Upcoming appointments:\n" + "\n".join(lines), {"appointments": structured}


def tool_find_care(
    db: Session, scope: dict, *, specialty_code: str | None = None,
    capabilities: list[str] | None = None, **_: Any
) -> tuple[str, dict]:
    """Capability-aware provider matching, reusing the production matcher."""
    profile = db.execute(
        select(PatientProfile).where(
            PatientProfile.user_id == scope["subject_user_id"]
        )
    ).scalar_one_or_none()

    criteria = MatchCriteria(
        specialty_code=specialty_code or "general_medicine",
        required_capabilities=capabilities or [],
        patient_lat=profile.latitude if profile else None,
        patient_lon=profile.longitude if profile else None,
    )
    doctors = match_doctors(db, criteria, limit=4)
    facilities = match_facilities(db, criteria, limit=3)

    structured = {
        "doctors": [
            {
                "doctor_id": m.doctor.id,
                "name": m.doctor.user.full_name if m.doctor.user else None,
                "specialty": m.doctor.specialty.name if m.doctor.specialty else None,
                "hospital_name": m.doctor.hospital.name if m.doctor.hospital else None,
                "distance_km": m.distance_km,
                "fee_lkr": m.doctor.consultation_fee_lkr,
                "next_available": m.next_slot.to_dict() if m.next_slot else None,
                "explanation": m.explanation,
            }
            for m in doctors
        ],
        "facilities": [
            {
                "hospital_id": f.hospital.id,
                "name": f.hospital.name,
                "city": f.hospital.city,
                "distance_km": f.distance_km,
                "has_emergency": f.hospital.has_emergency,
            }
            for f in facilities
        ],
    }

    specialty = SPECIALTY_BY_CODE.get(criteria.specialty_code)
    summary = (
        f"{len(doctors)} {specialty.name if specialty else criteria.specialty_code} "
        f"provider(s) available"
    )
    if doctors and doctors[0].next_slot:
        summary += f", earliest {doctors[0].next_slot.to_dict()['date_label']}"
    if criteria.required_capabilities:
        summary += f". Required facility services: {', '.join(criteria.required_capabilities)}"
    return summary + ".", structured


def tool_records(db: Session, scope: dict, **_: Any) -> tuple[str, dict]:
    """Recent reports and image screenings."""
    if not _has(scope, GuardianPermissionType.REPORTS):
        raise ToolDenied("This person has not shared their medical reports with you.")

    subject = scope["subject_user_id"]
    documents = db.execute(
        select(MedicalDocument)
        .options(selectinload(MedicalDocument.extracted))
        .where(MedicalDocument.patient_user_id == subject)
        .order_by(MedicalDocument.created_at.desc())
        .limit(3)
    ).scalars().unique().all()

    images = db.execute(
        select(MedicalImage)
        .options(selectinload(MedicalImage.analysis))
        .where(MedicalImage.patient_user_id == subject)
        .order_by(MedicalImage.created_at.desc())
        .limit(2)
    ).scalars().unique().all()

    structured = {
        "reports": [
            {
                "id": d.id,
                "title": d.extracted.report_title if d.extracted else d.file_name,
                "uploaded_at": d.created_at.isoformat(),
                "summary": d.extracted.summary if d.extracted else None,
                "key_findings": d.extracted.key_findings if d.extracted else [],
            }
            for d in documents
        ],
        "screenings": [
            {
                "id": i.id,
                "modality": str(i.modality),
                "finding": i.analysis.finding_label if i.analysis else None,
                "confidence": i.analysis.confidence if i.analysis else None,
                "is_uncertain": i.analysis.is_uncertain if i.analysis else None,
            }
            for i in images
        ],
    }

    if not documents and not images:
        return "No reports or screenings on file.", structured

    lines: list[str] = []
    for report in structured["reports"]:
        findings = ", ".join(
            f"{f.get('test')} {f.get('result')} ({f.get('flag')})"
            for f in (report["key_findings"] or [])[:4]
        )
        lines.append(
            f"- Report '{report['title']}': {report['summary'] or 'processed'}"
            + (f" Abnormal: {findings}." if findings else "")
        )
    for screening in structured["screenings"]:
        lines.append(
            f"- {screening['modality'].replace('_', ' ')} screening: "
            f"{screening['finding']} "
            f"(confidence {round((screening['confidence'] or 0) * 100)}%"
            + (", uncertain" if screening["is_uncertain"] else "") + ")"
        )
    return "\n".join(lines), structured


def tool_medications(db: Session, scope: dict, **_: Any) -> tuple[str, dict]:
    if not _has(scope, GuardianPermissionType.MEDICATIONS):
        raise ToolDenied("This person has not shared their medications with you.")

    rows = db.execute(
        select(Medication).where(
            Medication.patient_user_id == scope["subject_user_id"],
            Medication.is_active.is_(True),
        )
    ).scalars().all()

    structured = [
        {
            "id": m.id,
            "name": m.name,
            "dosage": m.dosage,
            "frequency": m.frequency_label,
            "is_critical": m.is_critical,
        }
        for m in rows
    ]
    if not structured:
        return "No active medications on file.", {"medications": []}
    text = "Active medications:\n" + "\n".join(
        f"- {m['name']} {m['dosage']}, {m['frequency']}"
        + (" (critical)" if m["is_critical"] else "")
        for m in structured
    )
    return text, {"medications": structured}


def tool_knowledge(db: Session, scope: dict, *, question: str = "", **_: Any) -> tuple[str, dict]:
    """Grounded retrieval from the curated health corpus."""
    grounding, citations = knowledge_service.build_context(question, limit=3)
    if not grounding:
        return "No matching guidance found in the knowledge base.", {"citations": []}
    return grounding, {"citations": citations}


def tool_recommendation(db: Session, scope: dict, **_: Any) -> tuple[str, dict]:
    """The patient's latest care recommendation, produced deterministically."""
    recommendation = db.execute(
        select(Recommendation)
        .where(
            Recommendation.patient_user_id == scope["subject_user_id"],
            Recommendation.is_active.is_(True),
        )
        .order_by(Recommendation.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if recommendation is None:
        return "No active care recommendation.", {"recommendation": None}

    structured = {
        "id": recommendation.id,
        "specialty_code": recommendation.specialty_code,
        "urgency": str(recommendation.urgency),
        "reason": recommendation.reason,
        "next_action": recommendation.suggested_next_action,
        "confidence": recommendation.confidence,
        "required_capabilities": recommendation.required_capabilities,
    }
    text = (
        f"Current recommendation: {structured['specialty_code'].replace('_', ' ')}, "
        f"urgency {structured['urgency']}. {structured['reason']} "
        f"Next step: {structured['next_action']}"
    )
    return text, {"recommendation": structured}


# Registry consulted by the agent nodes. Keys are stable tool names that
# appear in the SSE trace, so the UI can label each chip.
TOOLS = {
    "appointments": tool_appointments,
    "find_care": tool_find_care,
    "records": tool_records,
    "medications": tool_medications,
    "knowledge": tool_knowledge,
    "recommendation": tool_recommendation,
}

# Which tools each route is allowed to call.
ROUTE_TOOLS: dict[str, tuple[str, ...]] = {
    "clinical": ("recommendation", "knowledge"),
    "admin": ("appointments", "find_care"),
    "records": ("records", "medications"),
    "knowledge": ("knowledge",),
    "direct": (),
}
