"""Capability-aware provider and facility matching.

This is SuwaPath's core differentiator: results are not just "doctors with the
right specialty near you", they are providers at facilities that can actually
deliver the diagnostic step the recommendation calls for.

Every result carries a human-readable explanation of *why* it was matched
(spec §7) and a per-factor score breakdown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.clinical.catalog import CAPABILITY_BY_CODE, SPECIALTY_BY_CODE
from app.models.enums import FacilityType, UrgencyLevel, VerificationStatus, VisitType
from app.models.providers import Doctor, FacilityCapability, Hospital, Specialty
from app.services.availability import Slot, next_available_map

# Colombo city centre — fallback origin when a patient has no coordinates.
DEFAULT_ORIGIN = (6.9271, 79.8612)

# Relative weight of each ranking factor (spec §7 lists ten).
WEIGHTS = {
    "specialty": 30.0,
    "sub_specialty": 8.0,
    "availability": 18.0,
    "distance": 14.0,
    "capability": 16.0,
    "language": 7.0,
    "consultation_mode": 5.0,
    "experience": 5.0,
    "urgency_fit": 12.0,
    "verification": 4.0,
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(a))


@dataclass
class MatchCriteria:
    specialty_code: str
    secondary_specialty_codes: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    urgency: UrgencyLevel = UrgencyLevel.ROUTINE
    patient_lat: float | None = None
    patient_lon: float | None = None
    patient_language: str | None = None
    preferred_visit_type: VisitType | None = None
    max_distance_km: float | None = None
    max_fee_lkr: float | None = None
    sub_specialty: str | None = None

    @property
    def origin(self) -> tuple[float, float]:
        if self.patient_lat is not None and self.patient_lon is not None:
            return self.patient_lat, self.patient_lon
        return DEFAULT_ORIGIN

    @property
    def requires_emergency(self) -> bool:
        return (
            self.urgency is UrgencyLevel.EMERGENCY
            or "emergency" in self.required_capabilities
        )


@dataclass
class DoctorMatch:
    doctor: Doctor
    score: float
    distance_km: float | None
    next_slot: Slot | None
    matched_capabilities: list[str]
    missing_capabilities: list[str]
    reasons: list[str]
    factor_scores: dict[str, float]

    @property
    def explanation(self) -> str:
        if not self.reasons:
            return "Matched on general availability."
        return "Recommended because " + _join_clauses(self.reasons) + "."


@dataclass
class FacilityMatch:
    hospital: Hospital
    score: float
    distance_km: float
    matched_capabilities: list[str]
    missing_capabilities: list[str]
    available_doctor_count: int
    reasons: list[str]

    @property
    def explanation(self) -> str:
        if not self.reasons:
            return "Matched on location and available services."
        return "Recommended because " + _join_clauses(self.reasons) + "."


# --------------------------------------------------------------------------
# Doctor matching
# --------------------------------------------------------------------------
def match_doctors(
    db: Session, criteria: MatchCriteria, *, limit: int = 10
) -> list[DoctorMatch]:
    specialty_codes = [criteria.specialty_code, *criteria.secondary_specialty_codes]

    stmt = (
        select(Doctor)
        .join(Specialty, Doctor.specialty_id == Specialty.id)
        .options(
            selectinload(Doctor.user),
            selectinload(Doctor.specialty),
            selectinload(Doctor.schedules),
            selectinload(Doctor.hospital).selectinload(Hospital.capabilities),
        )
        .where(
            Doctor.is_active.is_(True),
            Doctor.accepts_new_patients.is_(True),
            Specialty.code.in_(specialty_codes),
        )
    )
    candidates = list(db.execute(stmt).scalars().unique())

    # Widen to general medicine if the specialty has no bookable doctors, so the
    # patient always gets a route forward rather than an empty state.
    if not candidates and "general_medicine" not in specialty_codes:
        stmt = (
            select(Doctor)
            .join(Specialty, Doctor.specialty_id == Specialty.id)
            .options(
                selectinload(Doctor.user),
                selectinload(Doctor.specialty),
                selectinload(Doctor.schedules),
                selectinload(Doctor.hospital).selectinload(Hospital.capabilities),
            )
            .where(Doctor.is_active.is_(True), Specialty.code == "general_medicine")
        )
        candidates = list(db.execute(stmt).scalars().unique())

    if criteria.max_fee_lkr is not None:
        candidates = [d for d in candidates if d.consultation_fee_lkr <= criteria.max_fee_lkr]

    slot_map = next_available_map(db, candidates)
    origin_lat, origin_lon = criteria.origin
    required = set(criteria.required_capabilities)

    matches: list[DoctorMatch] = []
    for doctor in candidates:
        distance = None
        if doctor.hospital:
            distance = haversine_km(
                origin_lat, origin_lon, doctor.hospital.latitude, doctor.hospital.longitude
            )
        if (
            criteria.max_distance_km is not None
            and distance is not None
            and distance > criteria.max_distance_km
            and not doctor.supports_teleconsultation
        ):
            continue

        facility_caps = doctor.hospital.capability_codes() if doctor.hospital else set()
        matched_caps = sorted(required & facility_caps)
        missing_caps = sorted(required - facility_caps)

        slot = slot_map.get(doctor.id)
        factors, reasons = _score_doctor(
            doctor=doctor,
            criteria=criteria,
            distance=distance,
            slot=slot,
            matched_caps=matched_caps,
            missing_caps=missing_caps,
        )
        matches.append(
            DoctorMatch(
                doctor=doctor,
                score=round(sum(factors.values()), 2),
                distance_km=round(distance, 1) if distance is not None else None,
                next_slot=slot,
                matched_capabilities=matched_caps,
                missing_capabilities=missing_caps,
                reasons=reasons,
                factor_scores={k: round(v, 2) for k, v in factors.items()},
            )
        )

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:limit]


def _score_doctor(
    *,
    doctor: Doctor,
    criteria: MatchCriteria,
    distance: float | None,
    slot: Slot | None,
    matched_caps: list[str],
    missing_caps: list[str],
) -> tuple[dict[str, float], list[str]]:
    factors: dict[str, float] = {}
    reasons: list[str] = []
    specialty_code = doctor.specialty.code if doctor.specialty else ""

    # 1. Specialty match
    if specialty_code == criteria.specialty_code:
        factors["specialty"] = WEIGHTS["specialty"]
        reasons.append(f"this doctor specialises in {doctor.specialty.name.lower()}")
    elif specialty_code in criteria.secondary_specialty_codes:
        factors["specialty"] = WEIGHTS["specialty"] * 0.6
        reasons.append(f"{doctor.specialty.name.lower()} is a relevant alternative specialty")
    else:
        factors["specialty"] = WEIGHTS["specialty"] * 0.25

    # 2. Sub-specialty
    if criteria.sub_specialty and doctor.sub_specialty:
        if criteria.sub_specialty.lower() in doctor.sub_specialty.lower():
            factors["sub_specialty"] = WEIGHTS["sub_specialty"]
            reasons.append(f"they sub-specialise in {doctor.sub_specialty.lower()}")

    # 3. Availability — sooner is better, and matters much more when urgent.
    if slot:
        from datetime import datetime, timezone

        hours_away = (slot.start - datetime.now(timezone.utc)).total_seconds() / 3600
        # Full marks within 24h, decaying to zero at ~10 days.
        freshness = max(0.0, 1.0 - max(0.0, hours_away - 24) / 216)
        urgency_multiplier = 1.4 if criteria.urgency.rank >= 2 else 1.0
        factors["availability"] = WEIGHTS["availability"] * freshness * urgency_multiplier
        reasons.append(f"they have an appointment {_humanise_when(hours_away)}")
    else:
        factors["availability"] = 0.0

    # 4. Distance — decays over 40 km.
    if distance is not None:
        factors["distance"] = WEIGHTS["distance"] * max(0.0, 1.0 - min(distance, 40) / 40)
        if distance <= 8:
            reasons.append(f"the facility is close to you ({distance:.1f} km)")
    else:
        factors["distance"] = WEIGHTS["distance"] * 0.3

    # 5. Facility capability — the differentiating factor.
    required_count = len(criteria.required_capabilities)
    if required_count:
        ratio = len(matched_caps) / required_count
        factors["capability"] = WEIGHTS["capability"] * ratio
        if matched_caps:
            names = _capability_names(matched_caps[:3])
            reasons.append(
                f"they practise at a facility providing {_join_clauses(names)}"
            )
    else:
        factors["capability"] = WEIGHTS["capability"] * 0.5

    # 6. Language
    languages = {str(lang).lower() for lang in (doctor.languages or [])}
    if criteria.patient_language and criteria.patient_language.lower() in languages:
        factors["language"] = WEIGHTS["language"]
        reasons.append(f"they consult in your preferred language")
    else:
        factors["language"] = WEIGHTS["language"] * 0.3

    # 7. Consultation mode
    if criteria.preferred_visit_type is VisitType.TELECONSULTATION:
        if doctor.supports_teleconsultation:
            factors["consultation_mode"] = WEIGHTS["consultation_mode"]
            reasons.append("they offer teleconsultation")
        else:
            factors["consultation_mode"] = 0.0
    else:
        factors["consultation_mode"] = (
            WEIGHTS["consultation_mode"] if doctor.supports_physical else 0.0
        )

    # 8. Experience and standing
    factors["experience"] = WEIGHTS["experience"] * min(1.0, doctor.years_experience / 20)
    if doctor.years_experience >= 15:
        reasons.append(f"they have {doctor.years_experience} years of experience")

    # 9. Urgency fit — emergency capability outranks preference (rule 3).
    if criteria.requires_emergency:
        has_emergency = bool(doctor.hospital and doctor.hospital.has_emergency)
        factors["urgency_fit"] = WEIGHTS["urgency_fit"] if has_emergency else 0.0
        if has_emergency:
            reasons.append("their hospital has a 24-hour emergency department")
    else:
        factors["urgency_fit"] = WEIGHTS["urgency_fit"] * 0.5

    # 10. Verification
    verified = str(doctor.verification_status) == str(VerificationStatus.VERIFIED)
    factors["verification"] = WEIGHTS["verification"] if verified else 0.0

    # Penalty for a facility that cannot deliver the required investigation.
    if missing_caps:
        factors["capability_gap_penalty"] = -3.0 * len(missing_caps)

    return factors, reasons[:4]


# --------------------------------------------------------------------------
# Facility matching
# --------------------------------------------------------------------------
def match_facilities(
    db: Session,
    criteria: MatchCriteria,
    *,
    facility_type: FacilityType | None = None,
    limit: int = 10,
) -> list[FacilityMatch]:
    stmt = (
        select(Hospital)
        .options(selectinload(Hospital.capabilities), selectinload(Hospital.doctors))
        .where(Hospital.is_active.is_(True))
    )
    if facility_type:
        stmt = stmt.where(Hospital.facility_type == str(facility_type))
    # An emergency recommendation only ever surfaces emergency-capable
    # facilities (internal rule 3).
    if criteria.requires_emergency:
        stmt = stmt.where(Hospital.has_emergency.is_(True))

    hospitals = list(db.execute(stmt).scalars().unique())
    origin_lat, origin_lon = criteria.origin
    required = set(criteria.required_capabilities)
    specialty = SPECIALTY_BY_CODE.get(criteria.specialty_code)

    matches: list[FacilityMatch] = []
    for hospital in hospitals:
        distance = haversine_km(
            origin_lat, origin_lon, hospital.latitude, hospital.longitude
        )
        if criteria.max_distance_km is not None and distance > criteria.max_distance_km:
            continue

        caps = hospital.capability_codes()
        matched_caps = sorted(required & caps)
        missing_caps = sorted(required - caps)
        reasons: list[str] = []
        score = 0.0

        # Capability coverage dominates facility ranking.
        if required:
            ratio = len(matched_caps) / len(required)
            score += 45.0 * ratio
            if matched_caps:
                reasons.append(
                    f"it provides {_join_clauses(_capability_names(matched_caps[:3]))}"
                )
        else:
            score += 22.0

        # Distance
        score += 25.0 * max(0.0, 1.0 - min(distance, 50) / 50)
        if distance <= 10:
            reasons.append(f"it is {distance:.1f} km from you")

        # Specialty presence on site
        doctor_count = sum(
            1
            for d in hospital.doctors
            if d.is_active and d.specialty and d.specialty.code == criteria.specialty_code
        )
        if doctor_count:
            score += min(15.0, 3.0 * doctor_count)
            label = specialty.name.lower() if specialty else "the recommended specialty"
            reasons.append(f"{doctor_count} {label} specialist(s) practise there")

        # Emergency readiness
        if criteria.requires_emergency and hospital.has_emergency:
            score += 20.0
            reasons.append("it has a 24-hour emergency department")
        elif hospital.has_emergency:
            score += 5.0

        if hospital.is_24_hours:
            score += 3.0
        score += 4.0 * (hospital.rating / 5.0)
        score -= 4.0 * len(missing_caps)

        matches.append(
            FacilityMatch(
                hospital=hospital,
                score=round(score, 2),
                distance_km=round(distance, 1),
                matched_capabilities=matched_caps,
                missing_capabilities=missing_caps,
                available_doctor_count=doctor_count,
                reasons=reasons[:3],
            )
        )

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:limit]


def match_diagnostic_centres(
    db: Session, criteria: MatchCriteria, *, limit: int = 10
) -> list[FacilityMatch]:
    """Facilities able to run the recommended investigations.

    Includes hospitals as well as standalone centres, because the patient cares
    about capability, not corporate structure.
    """
    diagnostic_caps = [
        c
        for c in criteria.required_capabilities
        if CAPABILITY_BY_CODE.get(c)
        and CAPABILITY_BY_CODE[c].category in ("imaging", "diagnostic")
    ]
    narrowed = MatchCriteria(
        specialty_code=criteria.specialty_code,
        secondary_specialty_codes=criteria.secondary_specialty_codes,
        required_capabilities=diagnostic_caps or criteria.required_capabilities,
        # A diagnostic booking is never itself an emergency admission.
        urgency=UrgencyLevel.ROUTINE,
        patient_lat=criteria.patient_lat,
        patient_lon=criteria.patient_lon,
        max_distance_km=criteria.max_distance_km,
    )
    return match_facilities(db, narrowed, limit=limit)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _capability_names(codes: list[str]) -> list[str]:
    return [
        CAPABILITY_BY_CODE[c].name.lower() if c in CAPABILITY_BY_CODE else c.replace("_", " ")
        for c in codes
    ]


def _join_clauses(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _humanise_when(hours_away: float) -> str:
    if hours_away < 6:
        return "within the next few hours"
    if hours_away < 24:
        return "later today"
    if hours_away < 48:
        return "tomorrow"
    days = int(hours_away // 24)
    return f"in {days} days"
