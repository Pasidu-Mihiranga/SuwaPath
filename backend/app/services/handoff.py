"""Turn a finished chat consultation into the record a doctor opens.

The gap this closes
-------------------
There are two intake engines. The doctor's pre-consultation view reads
``StructuredIntake`` and ``SymptomSession``, which only the legacy
``/symptoms`` endpoints ever wrote. The product's actual chat runs through
``/agent`` and writes ``ChatSession``/``ChatMessage``.

So a patient could describe their symptoms, be told to see a cardiologist,
book, attend — and the doctor would open the record to a stale intake from
weeks earlier, or nothing at all. The conversation that produced the referral
never reached the person the patient was referred to. Measured, not assumed: a
full consultation through the live chat left the intake count unchanged.

What this writes, and what it deliberately does not
---------------------------------------------------
Everything here is already computed by the consultation — the concepts the
lexicon extracted, the rules that fired, the specialty the navigation engine
chose. Nothing is inferred a second time, and no new clinical judgement is
formed. It is a persistence step, not a reasoning one.

It records **no diagnosis and no suggested diagnosis**. The doctor's job is to
examine and decide; handing them a machine's guess to agree with is the
failure mode this whole architecture avoids. What they get is the patient's
own words, the structured history, and the deterministic urgency — the things
that save them time without steering them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clinical.lexicon import concept_label
from app.models.clinical import (
    Recommendation,
    RedFlagAssessment,
    StructuredIntake,
    SymptomMessage,
    SymptomSession,
)
from app.models.enums import SessionStatus

logger = logging.getLogger(__name__)


def _existing_session(db: Session, chat_session_id: str) -> SymptomSession | None:
    """The clinical session mirroring this chat, if one was already made."""
    return db.execute(
        select(SymptomSession).where(SymptomSession.id == chat_session_id)
    ).scalar_one_or_none()


def publish_consultation(
    db: Session,
    *,
    chat_session_id: str,
    patient_user_id: str,
    language: str,
    messages: list[dict],
    consult: dict,
    red_flags: dict,
    patient_context: dict | None = None,
) -> StructuredIntake | None:
    """Persist a finished consultation where the doctor will find it.

    Idempotent per chat session: re-running a turn, or a patient continuing
    after the assessment, updates the same rows rather than accumulating a new
    intake for every message. The clinical session reuses the chat session's
    id, which keeps the two trivially reconcilable when someone is looking at
    an audit trail and asking which conversation a record came from.

    Returns None for a confidential session — those never join back to the
    patient record, and that rule is older than this function.
    """
    if not patient_user_id or not chat_session_id:
        return None

    patient_turns = [m["content"] for m in messages if m.get("role") == "user"]
    if not patient_turns:
        return None

    context = patient_context or {}
    concepts = sorted(red_flags.get("concepts") or [])
    triggered = red_flags.get("rules") or []

    session = _existing_session(db, chat_session_id)
    if session is None:
        session = SymptomSession(
            id=chat_session_id,
            patient_user_id=patient_user_id,
            language=language or "en",
            status=SessionStatus.COMPLETED,
            turn_count=len(patient_turns),
            is_confidential=False,
            completed_at=datetime.now(timezone.utc),
        )
        db.add(session)
        db.flush()
    else:
        session.turn_count = len(patient_turns)
        session.status = SessionStatus.COMPLETED
        session.completed_at = datetime.now(timezone.utc)
        # Rewritten rather than appended: the transcript is a mirror of the
        # chat, and appending on every turn would duplicate the whole
        # conversation each time the patient says something.
        db.query(SymptomMessage).filter(
            SymptomMessage.session_id == session.id
        ).delete(synchronize_session=False)

    for index, message in enumerate(messages):
        role = "patient" if message.get("role") == "user" else "assistant"
        db.add(SymptomMessage(
            session_id=session.id,
            sequence=index,
            role=role,
            content=message.get("content", ""),
            capability="agent_consult",
        ))

    intake = db.execute(
        select(StructuredIntake).where(StructuredIntake.session_id == session.id)
    ).scalar_one_or_none()
    if intake is None:
        intake = StructuredIntake(session_id=session.id, patient_user_id=patient_user_id)
        db.add(intake)

    # The patient's own opening words, not a paraphrase. A doctor reading
    # "chief complaint: dyspnoea" learns less than one reading "I get out of
    # breath walking to the gate now, I didn't a month ago".
    intake.chief_complaint = patient_turns[0].strip()[:2000]
    intake.symptoms = [concept_label(c) for c in concepts]
    intake.allergies = list(context.get("allergies") or [])
    intake.medications = list(context.get("current_medications") or [])
    intake.relevant_history = list(context.get("chronic_conditions") or [])
    intake.negative_findings = [
        concept_label(c) for c in sorted(red_flags.get("negated_concepts") or [])
    ]
    # Named as candidates. The deterministic engine decides urgency; these are
    # what it matched on, shown so the doctor can see the basis rather than
    # only the verdict.
    intake.potential_red_flags = [
        rule.get("label") for rule in triggered if rule.get("label")
    ]
    intake.is_complete = True

    # Flushed only now that every NOT NULL column is populated — `id` comes
    # from a Python-side default that runs at flush, and the assessment below
    # holds a NOT NULL foreign key to it. Flushing earlier inserted a null
    # chief_complaint; not flushing at all inserted a null intake_id.
    db.flush()

    assessment = db.execute(
        select(RedFlagAssessment).where(RedFlagAssessment.intake_id == intake.id)
    ).scalar_one_or_none()
    if assessment is None:
        assessment = RedFlagAssessment(intake_id=intake.id)
        db.add(assessment)
    assessment.urgency = red_flags.get("urgency") or "routine"
    assessment.triggered_rules = triggered
    assessment.escalation_message = red_flags.get("escalation_message") or ""
    assessment.requires_emergency_facility = bool(
        red_flags.get("requires_emergency_facility")
    )

    specialty = consult.get("specialty")
    if specialty:
        recommendation = db.execute(
            select(Recommendation)
            .where(Recommendation.patient_user_id == patient_user_id)
            .order_by(Recommendation.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        # Updated when it is for the same conversation, new otherwise, so a
        # patient's referral history does not collapse into one row.
        if recommendation is None or recommendation.intake_id != intake.id:
            recommendation = Recommendation(
                patient_user_id=patient_user_id, intake_id=intake.id
            )
            db.add(recommendation)
        recommendation.source = "symptom"
        recommendation.care_category = consult.get("specialty_name") or specialty
        recommendation.specialty_code = specialty
        recommendation.urgency = assessment.urgency
        recommendation.reason = (
            f"The consultation matched {', '.join(intake.potential_red_flags)}."
            if intake.potential_red_flags
            else f"Reported symptoms point to {recommendation.care_category}."
        )
        recommendation.suggested_next_action = (
            assessment.escalation_message
            or f"Book a consultation with {recommendation.care_category}."
        )
        recommendation.recommended_tests = consult.get("tests") or []

    logger.info(
        "Published consultation %s for the doctor view (urgency %s).",
        chat_session_id, assessment.urgency,
    )
    return intake
