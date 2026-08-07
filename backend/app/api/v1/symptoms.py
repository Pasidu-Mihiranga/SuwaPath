"""AI symptom conversation, structured intake and care recommendation.

Each turn runs the LangGraph care graph. The raw conversation and the AI-derived
structure are persisted to separate tables, so a clinician always retains the
patient's original words (spec §10).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import build_patient_context
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.clinical import (
    Recommendation,
    RedFlagAssessment,
    StructuredIntake,
    SymptomMessage,
    SymptomSession,
)
from app.models.enums import (
    Language,
    NotificationCategory,
    NotificationPriority,
    SessionStatus,
    UrgencyLevel,
    UserRole,
)
from app.models.identity import User
from app.models.platform import Notification
from app.models.providers import Specialty
from app.services.graph import run_turn

router = APIRouter(prefix="/symptoms", tags=["symptoms"])

MAX_TURNS = 6


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class StartSessionRequest(BaseModel):
    language: Language = Language.EN
    initial_message: str | None = Field(default=None, max_length=4000)


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class SessionSummary(BaseModel):
    id: str
    status: str
    language: str
    turn_count: int
    created_at: datetime
    chief_complaint: str | None = None
    urgency: str | None = None
    specialty_code: str | None = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _conversation(session: SymptomSession) -> list[dict]:
    return [
        {"role": m.role, "content": m.content, "meta": m.meta or {}}
        for m in sorted(session.messages, key=lambda m: m.sequence)
    ]


def _load_session(db: Session, session_id: str, user: User) -> SymptomSession:
    session = db.execute(
        select(SymptomSession)
        .options(selectinload(SymptomSession.messages))
        .where(SymptomSession.id == session_id)
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(status_code=404, detail="Symptom session not found.")
    if session.patient_user_id != user.id and str(user.role) != str(UserRole.SYSTEM_ADMIN):
        raise HTTPException(
            status_code=403, detail="You do not have access to this session."
        )
    return session


def _add_message(
    db: Session,
    session: SymptomSession,
    role: str,
    content: str,
    *,
    capability: str | None = None,
    meta: dict | None = None,
) -> SymptomMessage:
    sequence = len(session.messages)
    message = SymptomMessage(
        session_id=session.id,
        sequence=sequence,
        role=role,
        content=content,
        capability=capability,
        meta=meta or {},
    )
    db.add(message)
    session.messages.append(message)
    return message


def _persist_outcome(
    db: Session, session: SymptomSession, state: dict, user: User
) -> dict | None:
    """Persist structured intake, red flags and the recommendation."""
    intake_data = state.get("intake")
    flags = state.get("red_flags")
    recommendation_data = state.get("recommendation")
    if not (intake_data and flags and recommendation_data):
        return None

    intake = StructuredIntake(
        session_id=session.id,
        patient_user_id=session.patient_user_id,
        chief_complaint=intake_data.get("chief_complaint") or "Health concern",
        symptoms=intake_data.get("symptoms", []),
        duration_text=intake_data.get("duration_text"),
        duration_hours=intake_data.get("duration_hours"),
        severity=intake_data.get("severity"),
        associated_symptoms=intake_data.get("associated_symptoms", []),
        relevant_history=intake_data.get("relevant_history", []),
        medications=intake_data.get("medications", []),
        allergies=intake_data.get("allergies", []),
        potential_red_flags=[r["label"] for r in flags.get("triggered_rules", [])],
        onset=intake_data.get("onset"),
        aggravating_factors=intake_data.get("aggravating_factors", []),
        relieving_factors=intake_data.get("relieving_factors", []),
        negative_findings=intake_data.get("negative_findings", []),
        extraction_source=intake_data.get("extraction_source", "rule_based"),
        extraction_confidence=intake_data.get("extraction_confidence", 0.6),
        is_complete=True,
    )
    db.add(intake)
    db.flush()

    db.add(
        RedFlagAssessment(
            intake_id=intake.id,
            urgency=UrgencyLevel(flags["urgency"]),
            triggered_rules=flags.get("triggered_rules", []),
            escalation_message=flags.get("escalation_message", ""),
            requires_emergency_facility=flags.get("requires_emergency_facility", False),
            rule_engine_version=flags.get("engine_version", "1.0.0"),
        )
    )

    specialty = db.execute(
        select(Specialty).where(Specialty.code == recommendation_data["specialty_code"])
    ).scalar_one_or_none()

    recommendation = Recommendation(
        patient_user_id=session.patient_user_id,
        intake_id=intake.id,
        source="symptom",
        care_category=recommendation_data["care_category"],
        specialty_id=specialty.id if specialty else None,
        specialty_code=recommendation_data["specialty_code"],
        secondary_specialty_codes=recommendation_data.get("secondary_specialty_codes", []),
        urgency=UrgencyLevel(recommendation_data["urgency"]),
        reason=recommendation_data["reason"],
        suggested_next_action=recommendation_data["suggested_next_action"],
        confidence=recommendation_data["confidence"],
        required_capabilities=recommendation_data.get("required_capabilities", []),
        recommended_tests=recommendation_data.get("recommended_tests", []),
        patient_guidance=recommendation_data.get("patient_guidance"),
        knowledge_citations=state.get("citations", []),
    )
    db.add(recommendation)

    session.status = SessionStatus.COMPLETED
    session.completed_at = datetime.now(timezone.utc)

    # An emergency assessment raises a critical notification immediately.
    if flags["urgency"] == str(UrgencyLevel.EMERGENCY):
        db.add(
            Notification(
                user_id=session.patient_user_id,
                category=NotificationCategory.EMERGENCY,
                priority=NotificationPriority.CRITICAL,
                title="Seek urgent medical attention",
                body=flags.get("escalation_message", ""),
                action_type="recommendation",
                action_id=recommendation.id,
            )
        )

    db.flush()
    return {
        "intake_id": intake.id,
        "recommendation_id": recommendation.id,
        "specialty_name": specialty.name if specialty else None,
    }


def _serialise_turn(state: dict, session: SymptomSession, persisted: dict | None) -> dict:
    return {
        "session_id": session.id,
        "assistant_message": state.get("assistant_message", ""),
        "capability": state.get("capability"),
        "routing_rationale": state.get("routing_rationale"),
        "is_complete": bool(state.get("is_complete")),
        "turn_count": session.turn_count,
        "citations": state.get("citations", []),
        # The orchestration trace is surfaced so the flow is explainable.
        "orchestration_trace": state.get("trace", []),
        "red_flags": state.get("red_flags"),
        "intake": state.get("intake"),
        "recommendation": {
            **(state.get("recommendation") or {}),
            **(persisted or {}),
        } if state.get("recommendation") else None,
    }


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def start_session(
    payload: StartSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if str(current_user.role) != str(UserRole.PATIENT):
        raise HTTPException(
            status_code=403, detail="Only patients can start a symptom check."
        )

    session = SymptomSession(
        patient_user_id=current_user.id,
        language=payload.language,
        status=SessionStatus.ACTIVE,
    )
    db.add(session)
    db.flush()

    if not payload.initial_message:
        greeting = {
            Language.EN: "Hello. Tell me what health concern brings you here today.",
            Language.SI: "ආයුබෝවන්. අද ඔබට ඇති සෞඛ්‍ය ගැටලුව කුමක්දැයි කියන්න.",
            Language.TA: "வணக்கம். இன்று உங்களுக்கு என்ன உடல்நலப் பிரச்சினை என்று சொல்லுங்கள்.",
        }[payload.language]
        _add_message(db, session, "assistant", greeting, capability="symptom_intake")
        db.commit()
        return {
            "session_id": session.id,
            "assistant_message": greeting,
            "is_complete": False,
            "turn_count": 0,
        }

    db.commit()
    return send_message(
        session.id, MessageRequest(message=payload.initial_message), current_user, db
    )


@router.post("/sessions/{session_id}/messages")
def send_message(
    session_id: str,
    payload: MessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    session = _load_session(db, session_id, current_user)
    if session.status == SessionStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="This symptom check is already complete. Start a new one to continue.",
        )

    _add_message(db, session, "patient", payload.message.strip())
    session.turn_count += 1
    db.flush()

    state = run_turn(
        user_text=payload.message,
        conversation=_conversation(session),
        language=str(session.language),
        patient_context=build_patient_context(db, current_user.id),
        max_turns=MAX_TURNS,
    )

    assistant_message = state.get("assistant_message") or ""
    if assistant_message:
        _add_message(
            db,
            session,
            "assistant",
            assistant_message,
            capability=state.get("capability"),
            meta={"asked_about": state.get("asked_about", [])},
        )

    persisted = _persist_outcome(db, session, state, current_user)
    db.commit()
    return _serialise_turn(state, session, persisted)


@router.get("/sessions")
def list_sessions(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    sessions = db.execute(
        select(SymptomSession)
        .options(
            selectinload(SymptomSession.intake).selectinload(
                StructuredIntake.red_flag_assessment
            )
        )
        .where(SymptomSession.patient_user_id == current_user.id)
        .order_by(SymptomSession.created_at.desc())
        .limit(limit)
    ).scalars().unique()

    out = []
    for session in sessions:
        intake = session.intake
        assessment = intake.red_flag_assessment if intake else None
        recommendation = db.execute(
            select(Recommendation).where(Recommendation.intake_id == intake.id)
            if intake
            else select(Recommendation).where(Recommendation.id == "")
        ).scalar_one_or_none()

        out.append(
            {
                "id": session.id,
                "status": str(session.status),
                "language": str(session.language),
                "turn_count": session.turn_count,
                "created_at": session.created_at,
                "chief_complaint": intake.chief_complaint if intake else None,
                "urgency": str(assessment.urgency) if assessment else None,
                "specialty_code": recommendation.specialty_code if recommendation else None,
            }
        )
    return out


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    session = _load_session(db, session_id, current_user)
    intake = session.intake
    assessment = intake.red_flag_assessment if intake else None
    recommendation = (
        db.execute(
            select(Recommendation).where(Recommendation.intake_id == intake.id)
        ).scalar_one_or_none()
        if intake
        else None
    )

    return {
        "id": session.id,
        "status": str(session.status),
        "language": str(session.language),
        "turn_count": session.turn_count,
        "created_at": session.created_at,
        # Raw conversation, preserved verbatim.
        "messages": [
            {
                "sequence": m.sequence,
                "role": m.role,
                "content": m.content,
                "capability": m.capability,
                "created_at": m.created_at,
            }
            for m in sorted(session.messages, key=lambda m: m.sequence)
        ],
        "intake": _intake_dict(intake) if intake else None,
        "red_flags": _assessment_dict(assessment) if assessment else None,
        "recommendation": _recommendation_dict(recommendation) if recommendation else None,
    }


def _intake_dict(intake: StructuredIntake) -> dict:
    return {
        "id": intake.id,
        "chief_complaint": intake.chief_complaint,
        "symptoms": intake.symptoms,
        "duration_text": intake.duration_text,
        "severity": intake.severity,
        "associated_symptoms": intake.associated_symptoms,
        "relevant_history": intake.relevant_history,
        "medications": intake.medications,
        "allergies": intake.allergies,
        "negative_findings": intake.negative_findings,
        "potential_red_flags": intake.potential_red_flags,
        "extraction_source": intake.extraction_source,
        "extraction_confidence": intake.extraction_confidence,
    }


def _assessment_dict(assessment: RedFlagAssessment) -> dict:
    return {
        "urgency": str(assessment.urgency),
        "triggered_rules": assessment.triggered_rules,
        "escalation_message": assessment.escalation_message,
        "requires_emergency_facility": assessment.requires_emergency_facility,
        "rule_engine_version": assessment.rule_engine_version,
    }


def _recommendation_dict(recommendation: Recommendation) -> dict:
    return {
        "id": recommendation.id,
        "source": recommendation.source,
        "care_category": recommendation.care_category,
        "specialty_code": recommendation.specialty_code,
        "specialty_name": (
            recommendation.specialty.name if recommendation.specialty else None
        ),
        "secondary_specialty_codes": recommendation.secondary_specialty_codes,
        "urgency": str(recommendation.urgency),
        "reason": recommendation.reason,
        "suggested_next_action": recommendation.suggested_next_action,
        "confidence": recommendation.confidence,
        "required_capabilities": recommendation.required_capabilities,
        "recommended_tests": recommendation.recommended_tests,
        "patient_guidance": recommendation.patient_guidance,
        "knowledge_citations": recommendation.knowledge_citations,
        "created_at": recommendation.created_at,
    }
