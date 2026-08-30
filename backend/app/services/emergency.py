"""What happens when someone says it out loud instead of typing it.

The rest of the platform assumes a patient who can hold a phone, read a
screen and tap a button. The people most likely to be having the emergency
are the ones who can do none of those things: collapsed, bleeding, fitting,
or holding a child who has stopped feeding. This module is the path for them
— a spoken sentence is screened and, if it is an emergency, help is summoned
without anyone touching anything.

Three deliberate boundaries
---------------------------

**The decision is not AI.** Screening runs `red_flag_engine` over concepts the
lexicon extracted, exactly as a typed conversation does. A hands-free path
that could be talked into or out of an emergency by a language model would be
the most dangerous surface in the product; instead this is the same fixed
rules, reached by a different door. It is also why the endpoint is fast enough
to run on every sentence — there is no model call in it.

**"Calling the hospital" means alerting it, not dialling it.** A browser
cannot place a telephone call on its own, and a product that claimed to would
be lying to someone who is counting on it. What actually happens is that the
nearest emergency-capable facilities get an `inbound_emergency` alert on the
dashboard their staff already watch, and the patient gets a one-tap handoff to
1990. The ambulance service is summoned by a human — the app removes every
step before that one.

**The hospital is told who and what, never the words.** The transcript is
screened and discarded: it is not stored here, not written into an alert, and
not sent to a guardian. What travels is the patient's name, contact and city,
the rule ids that fired and the capabilities the receiving facility needs.
That is what a receiving team can act on, and it is the least that achieves
it.

Repetition is the failure mode this module exists to survive. Continuous
listening hears the same emergency in five consecutive sentences, and five
ambulance alerts for one event is how a real emergency service learns to
ignore SuwaPath. Dispatch is therefore idempotent inside a cooldown window:
the patient still sees the guidance every time, but the alerts go out once.
    40|"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    AlertSeverity,
    FacilityType,
    GuardianPermissionType,
    NotificationCategory,
    NotificationPriority,
    UrgencyLevel,
)
from app.models.identity import PatientProfile, User
from app.models.platform import HospitalAlert, Notification
from app.services import red_flag_engine as rfe
from app.services.alerts import raise_guardian_alerts
from app.services.matching import MatchCriteria, match_facilities

logger = logging.getLogger(__name__)

# Sri Lanka's national ambulance service: free, nationwide, no fee at point of
# use. The single number worth putting in front of everything else.
AMBULANCE_NUMBER = "1990"
AMBULANCE_NAME = "Suwa Seriya"

# How many facilities are alerted. Enough that one being overwhelmed is not
# fatal to the handoff; few enough that this is not a broadcast.
FACILITIES_ALERTED = 3

# Where the patient is pointed. More than the alerted set, because the list is
# also what they read out to whoever is driving.
FACILITIES_SHOWN = 5

# One spoken emergency is one dispatch. Long enough to cover a whole event
# including the patient repeating themselves to the ambulance controller;
# short enough that a second, separate emergency an hour later still gets
# through.
DISPATCH_COOLDOWN = timedelta(minutes=15)

# Marks the patient-facing notification that records a dispatch. Also the
# cooldown key — the notification is the record, so there is no second table
# to keep in step with it.
DISPATCH_ACTION = "voice_emergency_dispatch"

# Falls back to emergency medicine rather than general medicine: an emergency
# with no specialty hint is an undifferentiated one, and that is precisely the
# case for an emergency department.
DEFAULT_SPECIALTY = "emergency_medicine"


@dataclass
class DispatchResult:
    """What was actually done about a spoken emergency."""

    dispatched: bool
    hospitals: list[dict] = field(default_factory=list)
    hospitals_alerted: int = 0
    guardians_notified: int = 0
    # True when an identical dispatch is already live, so nothing was re-sent.
    already_active: bool = False


def screen(transcript: str, context: rfe.Context) -> rfe.RedFlagResult:
    """Triage one spoken sentence. Deterministic, no model, no side effects."""
    return rfe.assess_text(transcript or "", context)


def nearby_emergency_facilities(
    db: Session,
    result: rfe.RedFlagResult,
    *,
    latitude: float | None,
    longitude: float | None,
    language: str | None = None,
    limit: int = FACILITIES_SHOWN,
) -> list[dict]:
    """Emergency-capable facilities, nearest and best-equipped first.

    `match_facilities` already refuses to return a facility without an
    emergency department once the criteria are marked as an emergency, so the
    filter that matters is enforced by the matcher rather than repeated here.
    Capabilities come from the rules that fired: a stroke rule asks for a CT
    scanner, and a facility that has one should outrank a closer one that does
    not.
    """
    criteria = MatchCriteria(
        specialty_code=(
            result.specialty_hints[0] if result.specialty_hints else DEFAULT_SPECIALTY
        ),
        required_capabilities=result.required_capabilities,
        urgency=UrgencyLevel.EMERGENCY,
        patient_lat=latitude,
        patient_lon=longitude,
        patient_language=language,
    )
    matches = match_facilities(
        db, criteria, facility_type=FacilityType.HOSPITAL, limit=limit
    )
    return [
        {
            "hospital_id": m.hospital.id,
            "name": m.hospital.name,
            "city": m.hospital.city,
            "address": m.hospital.address,
            "phone": m.hospital.phone,
            "latitude": m.hospital.latitude,
            "longitude": m.hospital.longitude,
            "distance_km": m.distance_km,
            "has_emergency": m.hospital.has_emergency,
            "is_24_hours": m.hospital.is_24_hours,
            "explanation": m.explanation,
        }
        for m in matches
    ]


def dispatch(
    db: Session,
    *,
    patient: User,
    result: rfe.RedFlagResult,
    latitude: float | None = None,
    longitude: float | None = None,
) -> DispatchResult:
    """Summon help for a confirmed emergency. Caller commits the session.

    Callers must have established that `result.urgency` is EMERGENCY. This
    does not re-check it, because the one thing worse than a missed dispatch
    is two places deciding independently what counts as an emergency.
    """
    hospitals = nearby_emergency_facilities(
        db,
        result,
        latitude=latitude,
        longitude=longitude,
        language=str(patient.preferred_language),
    )

    if _dispatch_is_live(db, patient.id):
        return DispatchResult(
            dispatched=False, hospitals=hospitals, already_active=True
        )

    rule_labels = [r.label for r in result.triggered_rules[:3]]
    summary = "; ".join(rule_labels) or "Spoken emergency reported"

    alerted = _alert_facilities(
        db, patient=patient, result=result, hospitals=hospitals, summary=summary
    )

    guardians = raise_guardian_alerts(
        db,
        patient=patient,
        alert_type="voice_emergency",
        severity=AlertSeverity.CRITICAL,
        title=f"{patient.full_name} may be having an emergency",
        detail=(
            f"SuwaPath heard something matching an emergency pattern: {summary}. "
            f"{result.escalation_message} "
            f"{alerted} nearby emergency facility(ies) have been alerted. "
            f"Call them now if you can."
        ),
        permission=GuardianPermissionType.EMERGENCY_ALERTS,
        meta={
            "source": "voice",
            "urgency": str(result.urgency),
            "rule_ids": [r.rule_id for r in result.triggered_rules],
            "hospitals_alerted": alerted,
        },
    )

    db.add(
        Notification(
            user_id=patient.id,
            category=NotificationCategory.EMERGENCY,
            priority=NotificationPriority.CRITICAL,
            title="Emergency help has been alerted",
            body=(
                f"{result.escalation_message} Call {AMBULANCE_NUMBER} "
                f"({AMBULANCE_NAME}) now if you have not already."
            ),
            action_type=DISPATCH_ACTION,
            meta={
                "rule_ids": [r.rule_id for r in result.triggered_rules],
                "hospitals_alerted": alerted,
                "guardians_notified": guardians,
            },
        )
    )

    logger.warning(
        "Voice emergency dispatched for patient=%s rules=%s hospitals=%d guardians=%d",
        patient.id,
        [r.rule_id for r in result.triggered_rules],
        alerted,
        guardians,
    )

    return DispatchResult(
        dispatched=True,
        hospitals=hospitals,
        hospitals_alerted=alerted,
        guardians_notified=guardians,
    )


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------
def _dispatch_is_live(db: Session, patient_user_id: str) -> bool:
    """Has help already been summoned for this patient very recently?"""
    since = datetime.now(timezone.utc) - DISPATCH_COOLDOWN
    return (
        db.execute(
            select(Notification.id)
            .where(
                Notification.user_id == patient_user_id,
                Notification.action_type == DISPATCH_ACTION,
                Notification.created_at >= since,
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _alert_facilities(
    db: Session,
    *,
    patient: User,
    result: rfe.RedFlagResult,
    hospitals: list[dict],
    summary: str,
) -> int:
    """Put an inbound-emergency alert on the nearest facilities' dashboards.

    Contact details are included and clinical narrative is not. A receiving
    team needs to know who is coming and what to prepare; they do not need the
    sentence the patient said, and storing it here would put free-text PHI in
    a table read by every administrator at the facility.
    """
    profile = db.execute(
        select(PatientProfile).where(PatientProfile.user_id == patient.id)
    ).scalar_one_or_none()

    contact = patient.phone or (profile.emergency_contact_phone if profile else None)
    location = ", ".join(
        part for part in ((profile.city if profile else None), (profile.district if profile else None)) if part
    )

    alerted = 0
    for hospital in hospitals[:FACILITIES_ALERTED]:
        db.add(
            HospitalAlert(
                hospital_id=hospital["hospital_id"],
                alert_type="inbound_emergency",
                severity=AlertSeverity.CRITICAL,
                title=f"Inbound emergency — {patient.full_name}",
                detail=(
                    f"{summary}. "
                    f"Detected from a spoken report {hospital['distance_km']} km away"
                    + (f" ({location})" if location else "")
                    + ". "
                    f"Contact: {contact or 'none on file'}. "
                    f"Prepare: {', '.join(c.replace('_', ' ') for c in result.required_capabilities) or 'general emergency care'}."
                ),
                meta={
                    "source": "voice",
                    "patient_user_id": patient.id,
                    "patient_name": patient.full_name,
                    "patient_phone": contact,
                    "urgency": str(result.urgency),
                    "rule_ids": [r.rule_id for r in result.triggered_rules],
                    "required_capabilities": result.required_capabilities,
                    "distance_km": hospital["distance_km"],
                    "engine_version": result.engine_version,
                },
            )
        )
        alerted += 1
    return alerted
