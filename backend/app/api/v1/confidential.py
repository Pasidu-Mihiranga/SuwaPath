"""Confidential sexual-health mode.

No account required. A session is identified only by an opaque id plus a
recovery code whose hash alone is stored. Nothing here joins to `users`, so an
anonymous session can never be correlated with a normal patient record
(internal rule 7). The user can delete the session outright.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clinical.catalog import TEST_BY_CODE
from app.core.db import get_db
from app.core.security import generate_recovery_code, hash_recovery_code
from app.models.enums import (
    FacilityType,
    Language,
    SessionStatus,
    UrgencyLevel,
)
from app.models.platform import AnonymousHealthSession
from app.services.knowledge import knowledge_service
from app.services.matching import MatchCriteria, match_facilities

router = APIRouter(prefix="/confidential", tags=["confidential"])

SESSION_TTL_DAYS = 30

# Structured questions for the confidential intake (spec §16).
QUESTIONS = [
    {
        "code": "concern_type",
        "label": "What brings you here today?",
        "type": "choice",
        "options": [
            "I have symptoms",
            "I had a possible exposure",
            "I want routine testing",
            "I have a question",
        ],
    },
    {
        "code": "symptoms",
        "label": "Are you noticing any of these?",
        "type": "multi",
        "options": [
            "Unusual discharge",
            "Burning when passing urine",
            "Sores or ulcers",
            "Itching or rash",
            "Lower abdominal pain",
            "No symptoms",
        ],
    },
    {
        "code": "exposure_type",
        "label": "What kind of contact was involved?",
        "type": "choice",
        "options": ["Vaginal", "Anal", "Oral", "Prefer not to say", "Not applicable"],
    },
    {
        "code": "time_since_exposure",
        "label": "How long ago was the possible exposure?",
        "type": "choice",
        "options": [
            "Less than 72 hours",
            "3-14 days",
            "2-6 weeks",
            "More than 6 weeks",
            "Not applicable",
        ],
    },
    {
        "code": "protection_used",
        "label": "Was protection used?",
        "type": "choice",
        "options": ["Yes, throughout", "Partially", "No", "Prefer not to say"],
    },
    {
        "code": "previous_testing",
        "label": "Have you been tested before?",
        "type": "choice",
        "options": ["Never", "Within the last 6 months", "More than 6 months ago"],
    },
    {
        "code": "pregnancy_concern",
        "label": "Is pregnancy a concern for you?",
        "type": "choice",
        "options": ["Yes", "No", "Not applicable"],
    },
]


class StartRequest(BaseModel):
    language: Language = Language.EN
    approximate_city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    age_band: str | None = None


class ResumeRequest(BaseModel):
    recovery_code: str


class AnswersRequest(BaseModel):
    answers: dict = Field(default_factory=dict)


def _load(db: Session, session_id: str) -> AnonymousHealthSession:
    session = db.get(AnonymousHealthSession, session_id)
    if session is None or session.deleted_at is not None:
        raise HTTPException(
            status_code=404, detail="This private session no longer exists."
        )
    return session


def _guidance(answers: dict) -> dict:
    """Testing guidance derived from the structured answers.

    Window periods follow standard public-health guidance; a very recent
    high-risk exposure is escalated because post-exposure prophylaxis is
    time-limited to 72 hours.
    """
    timing = answers.get("time_since_exposure")
    protection = answers.get("protection_used")
    symptoms = answers.get("symptoms") or []
    has_symptoms = bool([s for s in symptoms if s and s != "No symptoms"])

    urgency = UrgencyLevel.ROUTINE
    notes: list[str] = []
    tests = ["sti_panel", "hiv_test", "vdrl"]

    if timing == "Less than 72 hours" and protection in ("No", "Partially"):
        urgency = UrgencyLevel.URGENT
        notes.append(
            "Because the possible exposure was within the last 72 hours, "
            "post-exposure prophylaxis (PEP) may still be an option. This is "
            "time-limited — contact a sexual-health service today rather than "
            "waiting to be tested."
        )
        notes.append(
            "Testing done now can still miss a very recent infection, so a "
            "repeat test later is usually advised."
        )
    elif timing == "3-14 days":
        notes.append(
            "Chlamydia and gonorrhoea testing is generally reliable from about "
            "two weeks after exposure. HIV and syphilis testing needs longer, "
            "usually four to six weeks, so a second test may be recommended."
        )
    elif timing == "2-6 weeks":
        notes.append(
            "This is a suitable time for most screening tests. A confirmatory "
            "HIV test at twelve weeks is often recommended."
        )
    elif timing == "More than 6 weeks":
        notes.append(
            "Enough time has passed for standard screening tests to be reliable."
        )

    if has_symptoms:
        urgency = max(urgency, UrgencyLevel.URGENT, key=lambda u: u.rank)
        notes.append(
            "Because you have symptoms, an in-person assessment is recommended "
            "rather than testing alone — some conditions need examination and "
            "treatment can often start the same day."
        )

    if answers.get("pregnancy_concern") == "Yes":
        tests.append("urine_fr")
        notes.append(
            "If pregnancy is a concern, emergency contraception is most "
            "effective the sooner it is taken. A pharmacist or clinic can advise."
        )

    if not notes:
        notes.append(
            "Routine screening is a sensible step even without symptoms, since "
            "many sexually transmitted infections cause none."
        )

    return {
        "urgency": urgency,
        "guidance": " ".join(notes),
        "tests": [
            {
                "code": code,
                "name": TEST_BY_CODE[code].name,
                "typical_price_lkr": TEST_BY_CODE[code].price_lkr,
            }
            for code in tests
            if code in TEST_BY_CODE
        ],
    }


def _serialise(session: AnonymousHealthSession, *, recovery_code: str | None = None) -> dict:
    return {
        "session_id": session.id,
        # Shown once, at creation. Only its hash is stored.
        "recovery_code": recovery_code,
        "display_alias": session.display_alias,
        "language": str(session.language),
        "status": str(session.status),
        "questions": QUESTIONS,
        "answers": session.structured_answers or {},
        "testing_guidance": session.testing_guidance,
        "suggested_specialty_code": session.suggested_specialty_code,
        "recommended_tests": session.recommended_tests or [],
        "expires_at": session.expires_at,
        "created_at": session.created_at,
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def start_session(payload: StartRequest, db: Session = Depends(get_db)) -> dict:
    """Begin a private session. No account, no identifying details."""
    code = generate_recovery_code()
    now = datetime.now(timezone.utc)

    session = AnonymousHealthSession(
        recovery_code_hash=hash_recovery_code(code),
        display_alias="Private session",
        language=payload.language,
        status=SessionStatus.ACTIVE,
        approximate_city=payload.approximate_city,
        latitude=payload.latitude,
        longitude=payload.longitude,
        age_band=payload.age_band,
        structured_answers={},
        last_active_at=now,
        expires_at=now + timedelta(days=SESSION_TTL_DAYS),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    result = _serialise(session, recovery_code=code)
    result["notice"] = (
        "Save this recovery code now. It is the only way to return to this "
        "session, and we cannot recover it for you. Nothing here is linked to "
        "your SuwaPath account."
    )
    return result


@router.post("/sessions/resume")
def resume_session(payload: ResumeRequest, db: Session = Depends(get_db)) -> dict:
    """Resume using the recovery code. Lookup is by hash only."""
    session = db.execute(
        select(AnonymousHealthSession).where(
            AnonymousHealthSession.recovery_code_hash
            == hash_recovery_code(payload.recovery_code),
            AnonymousHealthSession.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="No private session matches that recovery code.",
        )
    if session.expires_at and session.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="This private session has expired.")

    session.last_active_at = datetime.now(timezone.utc)
    db.commit()
    return _serialise(session)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: Session = Depends(get_db)) -> dict:
    return _serialise(_load(db, session_id))


@router.post("/sessions/{session_id}/answers")
def submit_answers(
    session_id: str, payload: AnswersRequest, db: Session = Depends(get_db)
) -> dict:
    """Record answers and produce confidential testing guidance."""
    session = _load(db, session_id)

    merged = {**(session.structured_answers or {}), **payload.answers}
    session.structured_answers = merged
    session.last_active_at = datetime.now(timezone.utc)

    outcome = _guidance(merged)
    session.testing_guidance = outcome["guidance"]
    session.suggested_specialty_code = "sexual_health"
    session.recommended_tests = outcome["tests"]

    grounding, citations = knowledge_service.build_context(
        "sexually transmitted infection testing and window period", limit=2
    )
    db.commit()
    db.refresh(session)

    result = _serialise(session)
    result["urgency"] = str(outcome["urgency"])
    result["knowledge_citations"] = citations
    return result


@router.get("/sessions/{session_id}/facilities")
def confidential_facilities(
    session_id: str, limit: int = 8, db: Session = Depends(get_db)
) -> dict:
    """Facilities offering confidential STI testing near the session's area."""
    session = _load(db, session_id)

    criteria = MatchCriteria(
        specialty_code="sexual_health",
        required_capabilities=["sti_testing", "laboratory"],
        urgency=UrgencyLevel.ROUTINE,
        patient_lat=session.latitude,
        patient_lon=session.longitude,
    )
    matches = match_facilities(db, criteria, limit=limit)

    return {
        "session_id": session.id,
        "count": len(matches),
        "results": [
            {
                "hospital_id": m.hospital.id,
                "name": m.hospital.name,
                "facility_type": str(m.hospital.facility_type),
                "city": m.hospital.city,
                "address": m.hospital.address,
                "phone": m.hospital.phone,
                "distance_km": m.distance_km,
                "explanation": m.explanation,
                "offers_confidential_testing": "sti_testing" in m.hospital.capability_codes(),
                "latitude": m.hospital.latitude,
                "longitude": m.hospital.longitude,
            }
            for m in matches
        ],
        "privacy_note": (
            "These services are confidential. You can attend without giving "
            "your SuwaPath account details."
        ),
    }


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)) -> dict:
    """Delete a private session and everything it contains."""
    session = _load(db, session_id)

    # Hard-clear the content rather than only flagging it, so nothing sensitive
    # survives in the row.
    session.structured_answers = {}
    session.testing_guidance = None
    session.recommended_tests = []
    session.approximate_city = None
    session.latitude = None
    session.longitude = None
    session.age_band = None
    session.recovery_code_hash = "deleted"
    session.status = SessionStatus.ABANDONED
    session.deleted_at = datetime.now(timezone.utc)

    db.commit()
    return {
        "session_id": session_id,
        "deleted": True,
        "message": "This private session and its contents have been deleted.",
    }


@router.get("/questions")
def questions() -> list[dict]:
    return QUESTIONS
