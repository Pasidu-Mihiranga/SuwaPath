"""Hands-free emergency screening for spoken input.

One endpoint, called on every sentence the always-on listener hears. It has to
be cheap enough to run that often and honest enough to act on, which is why it
is the deterministic rule engine and nothing else — no model, no retrieval, no
network hop beyond the database.

The asymmetry that shapes the whole design: a false positive costs an
apologetic notification and a dismissed overlay, while a false negative costs
the thing the feature exists to prevent. So screening errs toward hearing an
emergency, and everything expensive or irreversible — alerting a facility,
waking a guardian — happens once per event rather than once per sentence.

Nothing is written when the answer is "not an emergency". A listener running
all day would otherwise fill the database with a record of every sentence
spoken near the phone, which is not a thing this product should hold.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import build_patient_context
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.enums import GuardianPermissionType, UrgencyLevel
from app.models.identity import GuardianRelationship, User
from app.services import emergency
from app.services.red_flag_engine import build_context

router = APIRouter(prefix="/emergency", tags=["emergency"])


class VoiceScreenRequest(BaseModel):
    """One heard utterance, plus where the phone thinks it is."""

    transcript: str = Field(min_length=1, max_length=2000)
    # Live device coordinates beat the address on file: the emergency is where
    # the patient is standing, which is often not where they live.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    # Off for the "try it without summoning anyone" path, so the feature can be
    # demonstrated and tested without alerting a real emergency department.
    dispatch: bool = True


@router.post("/voice")
def screen_voice(
    payload: VoiceScreenRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Triage a spoken sentence and, if it is an emergency, summon help."""
    context_data = build_patient_context(db, current_user.id)
    result = emergency.screen(
        payload.transcript,
        build_context(
            age=context_data.get("age"),
            sex=context_data.get("sex"),
            is_pregnant=bool(context_data.get("is_pregnant")),
            is_postpartum=bool(context_data.get("is_postpartum")),
            pregnancy_week=context_data.get("pregnancy_week"),
            chronic_conditions=context_data.get("chronic_conditions"),
        ),
    )

    response = {
        "triggered": result.urgency == UrgencyLevel.EMERGENCY,
        "urgency": str(result.urgency),
        # Rule ids are what the avatar maps to a first-aid script, so they are
        # returned even when nothing fired — the client should never have to
        # guess which key it is missing.
        "rules": result.rules_as_dicts(),
        "engine_version": result.engine_version,
    }

    if result.urgency != UrgencyLevel.EMERGENCY:
        # Deliberately nothing else: no record, no alert, no stored transcript.
        return response

    latitude = payload.latitude if payload.latitude is not None else context_data.get("latitude")
    longitude = payload.longitude if payload.longitude is not None else context_data.get("longitude")

    if payload.dispatch:
        outcome = emergency.dispatch(
            db,
            patient=current_user,
            result=result,
            latitude=latitude,
            longitude=longitude,
        )
        db.commit()
    else:
        outcome = emergency.DispatchResult(
            dispatched=False,
            hospitals=emergency.nearby_emergency_facilities(
                db, result, latitude=latitude, longitude=longitude
            ),
        )

    return {
        **response,
        "escalation_message": result.escalation_message,
        "ambulance_number": emergency.AMBULANCE_NUMBER,
        "ambulance_name": emergency.AMBULANCE_NAME,
        "required_capabilities": result.required_capabilities,
        "dispatched": outcome.dispatched,
        "already_active": outcome.already_active,
        "hospitals_alerted": outcome.hospitals_alerted,
        "guardians_notified": outcome.guardians_notified,
        "hospitals": outcome.hospitals,
    }


@router.get("/readiness")
def readiness(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """What would actually happen if the listener fired right now.

    Exists so the toggle can tell the truth before anything happens. "Your
    daughter Nirmali will be told" is a reason to leave the microphone on;
    "listening" on its own is not, and a patient who does not know a guardian
    will be woken has not really consented to it.
    """
    relationships = db.execute(
        select(GuardianRelationship)
        .options(
            selectinload(GuardianRelationship.permissions),
            selectinload(GuardianRelationship.guardian),
        )
        .where(
            GuardianRelationship.patient_user_id == current_user.id,
            GuardianRelationship.is_active.is_(True),
        )
    ).scalars().unique().all()

    guardians = [
        {
            "name": r.guardian.full_name if r.guardian else "A guardian",
            "relationship": r.relationship_label,
        }
        for r in relationships
        if GuardianPermissionType.EMERGENCY_ALERTS in r.granted_scopes()
        or GuardianPermissionType.FULL_MEDICAL in r.granted_scopes()
    ]

    context_data = build_patient_context(db, current_user.id)

    return {
        "ambulance_number": emergency.AMBULANCE_NUMBER,
        "ambulance_name": emergency.AMBULANCE_NAME,
        "guardians_who_would_be_told": guardians,
        "facilities_alerted_per_event": emergency.FACILITIES_ALERTED,
        "cooldown_minutes": int(emergency.DISPATCH_COOLDOWN.total_seconds() // 60),
        # Whether we can rank facilities by distance at all without the browser
        # handing over live coordinates.
        "has_location_on_file": context_data.get("latitude") is not None,
    }
