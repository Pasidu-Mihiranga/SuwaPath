"""Capability-aware doctor, hospital and diagnostic-centre matching."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_patient_profile
from app.clinical.catalog import CAPABILITY_BY_CODE, SPECIALTIES, SPECIALTY_BY_CODE
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.clinical import Recommendation
from app.models.enums import FacilityType, UrgencyLevel, VisitType
from app.models.identity import User
from app.models.providers import Doctor, DiagnosticTest, Hospital, Specialty
from app.services.availability import generate_slots_for_doctor
from app.services.matching import (
    DoctorMatch,
    FacilityMatch,
    MatchCriteria,
    match_diagnostic_centres,
    match_doctors,
    match_facilities,
)

router = APIRouter(prefix="/providers", tags=["providers"])


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------
def _doctor_dict(match: DoctorMatch) -> dict:
    doctor = match.doctor
    slot = match.next_slot
    return {
        "doctor_id": doctor.id,
        "name": doctor.user.full_name if doctor.user else "Unknown",
        "avatar_url": doctor.user.avatar_url if doctor.user else None,
        "specialty_code": doctor.specialty.code if doctor.specialty else None,
        "specialty_name": doctor.specialty.name if doctor.specialty else None,
        "sub_specialty": doctor.sub_specialty,
        "hospital_id": doctor.hospital_id,
        "hospital_name": doctor.hospital.name if doctor.hospital else None,
        "hospital_city": doctor.hospital.city if doctor.hospital else None,
        "years_experience": doctor.years_experience,
        "qualifications": doctor.qualifications or [],
        "languages": doctor.languages or [],
        "rating": doctor.rating,
        "verification_status": str(doctor.verification_status),
        "supports_teleconsultation": doctor.supports_teleconsultation,
        "supports_physical": doctor.supports_physical,
        "consultation_fee_lkr": doctor.consultation_fee_lkr,
        "teleconsultation_fee_lkr": doctor.teleconsultation_fee_lkr,
        "distance_km": match.distance_km,
        "next_available": slot.to_dict() if slot else None,
        "match_score": match.score,
        # Why this doctor was recommended (spec §7).
        "explanation": match.explanation,
        "matched_capabilities": [
            CAPABILITY_BY_CODE[c].name if c in CAPABILITY_BY_CODE else c
            for c in match.matched_capabilities
        ],
        "missing_capabilities": [
            CAPABILITY_BY_CODE[c].name if c in CAPABILITY_BY_CODE else c
            for c in match.missing_capabilities
        ],
        "factor_scores": match.factor_scores,
    }


def _facility_dict(match: FacilityMatch) -> dict:
    hospital = match.hospital
    return {
        "hospital_id": hospital.id,
        "name": hospital.name,
        "facility_type": str(hospital.facility_type),
        "city": hospital.city,
        "district": hospital.district,
        "address": hospital.address,
        "latitude": hospital.latitude,
        "longitude": hospital.longitude,
        "phone": hospital.phone,
        "has_emergency": hospital.has_emergency,
        "is_24_hours": hospital.is_24_hours,
        "rating": hospital.rating,
        "bed_count": hospital.bed_count,
        "icu_bed_count": hospital.icu_bed_count,
        "distance_km": match.distance_km,
        "match_score": match.score,
        "explanation": match.explanation,
        "available_doctor_count": match.available_doctor_count,
        "matched_capabilities": [
            CAPABILITY_BY_CODE[c].name if c in CAPABILITY_BY_CODE else c
            for c in match.matched_capabilities
        ],
        "missing_capabilities": [
            CAPABILITY_BY_CODE[c].name if c in CAPABILITY_BY_CODE else c
            for c in match.missing_capabilities
        ],
        "all_capabilities": sorted(hospital.capability_codes()),
    }


# --------------------------------------------------------------------------
# Criteria construction
# --------------------------------------------------------------------------
def _criteria(
    db: Session,
    user: User,
    *,
    recommendation_id: str | None,
    specialty_code: str | None,
    capabilities: list[str] | None,
    urgency: str | None,
    visit_type: VisitType | None,
    max_distance_km: float | None,
    max_fee_lkr: float | None,
    sub_specialty: str | None,
) -> tuple[MatchCriteria, Recommendation | None]:
    recommendation = None
    resolved_specialty = specialty_code or "general_medicine"
    secondary: list[str] = []
    required = list(capabilities or [])
    resolved_urgency = UrgencyLevel(urgency) if urgency else UrgencyLevel.ROUTINE

    if recommendation_id:
        recommendation = db.get(Recommendation, recommendation_id)
        if recommendation is None:
            raise HTTPException(status_code=404, detail="Recommendation not found.")
        if (
            recommendation.patient_user_id
            and recommendation.patient_user_id != user.id
            and str(user.role) not in ("system_admin", "guardian")
        ):
            raise HTTPException(
                status_code=403, detail="This recommendation belongs to another patient."
            )
        # An explicit specialty filter from the UI overrides the recommendation,
        # letting the patient switch specialty while keeping its capabilities.
        resolved_specialty = specialty_code or recommendation.specialty_code
        secondary = recommendation.secondary_specialty_codes or []
        required = list(capabilities or recommendation.required_capabilities or [])
        resolved_urgency = UrgencyLevel(str(recommendation.urgency))

    profile = get_patient_profile(db, user.id)
    return (
        MatchCriteria(
            specialty_code=resolved_specialty,
            secondary_specialty_codes=secondary,
            required_capabilities=required,
            urgency=resolved_urgency,
            patient_lat=profile.latitude if profile else None,
            patient_lon=profile.longitude if profile else None,
            patient_language=str(user.preferred_language),
            preferred_visit_type=visit_type,
            max_distance_km=max_distance_km,
            max_fee_lkr=max_fee_lkr,
            sub_specialty=sub_specialty,
        ),
        recommendation,
    )


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@router.get("/doctors")
def find_doctors(
    recommendation_id: str | None = None,
    specialty_code: str | None = None,
    capabilities: list[str] | None = Query(default=None),
    urgency: str | None = None,
    visit_type: VisitType | None = None,
    max_distance_km: float | None = None,
    max_fee_lkr: float | None = None,
    sub_specialty: str | None = None,
    limit: int = Query(default=10, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    criteria, recommendation = _criteria(
        db, current_user,
        recommendation_id=recommendation_id, specialty_code=specialty_code,
        capabilities=capabilities, urgency=urgency, visit_type=visit_type,
        max_distance_km=max_distance_km, max_fee_lkr=max_fee_lkr,
        sub_specialty=sub_specialty,
    )
    matches = match_doctors(db, criteria, limit=limit)
    return {
        "criteria": {
            "specialty_code": criteria.specialty_code,
            "specialty_name": (
                SPECIALTY_BY_CODE[criteria.specialty_code].name
                if criteria.specialty_code in SPECIALTY_BY_CODE
                else criteria.specialty_code
            ),
            "urgency": str(criteria.urgency),
            "required_capabilities": criteria.required_capabilities,
            "requires_emergency": criteria.requires_emergency,
        },
        "recommendation_reason": recommendation.reason if recommendation else None,
        "count": len(matches),
        "results": [_doctor_dict(m) for m in matches],
    }


@router.get("/hospitals")
def find_hospitals(
    recommendation_id: str | None = None,
    specialty_code: str | None = None,
    capabilities: list[str] | None = Query(default=None),
    urgency: str | None = None,
    max_distance_km: float | None = None,
    limit: int = Query(default=10, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    criteria, recommendation = _criteria(
        db, current_user,
        recommendation_id=recommendation_id, specialty_code=specialty_code,
        capabilities=capabilities, urgency=urgency, visit_type=None,
        max_distance_km=max_distance_km, max_fee_lkr=None, sub_specialty=None,
    )
    matches = match_facilities(db, criteria, facility_type=FacilityType.HOSPITAL, limit=limit)
    return {
        "criteria": {
            "specialty_code": criteria.specialty_code,
            "urgency": str(criteria.urgency),
            "required_capabilities": criteria.required_capabilities,
            "requires_emergency": criteria.requires_emergency,
        },
        "recommendation_reason": recommendation.reason if recommendation else None,
        "count": len(matches),
        "results": [_facility_dict(m) for m in matches],
    }


@router.get("/diagnostic-centres")
def find_diagnostic_centres(
    recommendation_id: str | None = None,
    capabilities: list[str] | None = Query(default=None),
    max_distance_km: float | None = None,
    limit: int = Query(default=10, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    criteria, recommendation = _criteria(
        db, current_user,
        recommendation_id=recommendation_id, specialty_code=None,
        capabilities=capabilities, urgency=None, visit_type=None,
        max_distance_km=max_distance_km, max_fee_lkr=None, sub_specialty=None,
    )
    matches = match_diagnostic_centres(db, criteria, limit=limit)
    return {
        "criteria": {"required_capabilities": criteria.required_capabilities},
        "recommendation_reason": recommendation.reason if recommendation else None,
        "count": len(matches),
        "results": [_facility_dict(m) for m in matches],
    }


@router.get("/doctors/{doctor_id}")
def get_doctor(
    doctor_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    doctor = db.execute(
        select(Doctor)
        .options(
            selectinload(Doctor.user),
            selectinload(Doctor.specialty),
            selectinload(Doctor.schedules),
            selectinload(Doctor.hospital).selectinload(Hospital.capabilities),
        )
        .where(Doctor.id == doctor_id)
    ).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    return {
        "doctor_id": doctor.id,
        "name": doctor.user.full_name if doctor.user else "Unknown",
        "bio": doctor.bio,
        "specialty_code": doctor.specialty.code if doctor.specialty else None,
        "specialty_name": doctor.specialty.name if doctor.specialty else None,
        "sub_specialty": doctor.sub_specialty,
        "qualifications": doctor.qualifications or [],
        "languages": doctor.languages or [],
        "years_experience": doctor.years_experience,
        "rating": doctor.rating,
        "total_consultations": doctor.total_consultations,
        "verification_status": str(doctor.verification_status),
        "slmc_registration_no": doctor.slmc_registration_no,
        "consultation_fee_lkr": doctor.consultation_fee_lkr,
        "teleconsultation_fee_lkr": doctor.teleconsultation_fee_lkr,
        "supports_teleconsultation": doctor.supports_teleconsultation,
        "hospital": (
            {
                "id": doctor.hospital.id,
                "name": doctor.hospital.name,
                "city": doctor.hospital.city,
                "address": doctor.hospital.address,
                "capabilities": sorted(doctor.hospital.capability_codes()),
            }
            if doctor.hospital
            else None
        ),
        "weekly_schedule": [
            {
                "day_of_week": s.day_of_week,
                "start_time": s.start_time.strftime("%H:%M"),
                "end_time": s.end_time.strftime("%H:%M"),
                "visit_type": str(s.visit_type),
                "slot_duration_minutes": s.slot_duration_minutes,
            }
            for s in sorted(doctor.schedules, key=lambda s: (s.day_of_week, s.start_time))
            if s.is_active
        ],
    }


@router.get("/doctors/{doctor_id}/slots")
def doctor_slots(
    doctor_id: str,
    days: int = Query(default=14, le=60),
    visit_type: VisitType | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    doctor = db.execute(
        select(Doctor)
        .options(selectinload(Doctor.schedules))
        .where(Doctor.id == doctor_id)
    ).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    slots = generate_slots_for_doctor(db, doctor, days=days, visit_type=visit_type)

    # Group by date so the UI can render a day picker directly.
    grouped: dict[str, list[dict]] = {}
    for slot in slots:
        grouped.setdefault(slot.start.date().isoformat(), []).append(slot.to_dict())

    return {
        "doctor_id": doctor_id,
        "days": [
            {"date": day, "slots": day_slots} for day, day_slots in sorted(grouped.items())
        ],
        "total_slots": len(slots),
    }


@router.get("/hospitals/{hospital_id}")
def get_hospital(
    hospital_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    hospital = db.execute(
        select(Hospital)
        .options(
            selectinload(Hospital.capabilities),
            selectinload(Hospital.doctors).selectinload(Doctor.user),
            selectinload(Hospital.doctors).selectinload(Doctor.specialty),
        )
        .where(Hospital.id == hospital_id)
    ).scalar_one_or_none()
    if hospital is None:
        raise HTTPException(status_code=404, detail="Facility not found.")

    return {
        "hospital_id": hospital.id,
        "name": hospital.name,
        "facility_type": str(hospital.facility_type),
        "city": hospital.city,
        "district": hospital.district,
        "address": hospital.address,
        "latitude": hospital.latitude,
        "longitude": hospital.longitude,
        "phone": hospital.phone,
        "email": hospital.email,
        "is_24_hours": hospital.is_24_hours,
        "has_emergency": hospital.has_emergency,
        "opening_time": hospital.opening_time.strftime("%H:%M") if hospital.opening_time else None,
        "closing_time": hospital.closing_time.strftime("%H:%M") if hospital.closing_time else None,
        "bed_count": hospital.bed_count,
        "icu_bed_count": hospital.icu_bed_count,
        "rating": hospital.rating,
        "capabilities": [
            {"code": c.capability_code, "name": c.capability_name, "category": c.category}
            for c in hospital.capabilities
            if c.is_available
        ],
        "doctors": [
            {
                "doctor_id": d.id,
                "name": d.user.full_name if d.user else None,
                "specialty_name": d.specialty.name if d.specialty else None,
                "sub_specialty": d.sub_specialty,
                "years_experience": d.years_experience,
            }
            for d in hospital.doctors
            if d.is_active
        ][:40],
    }


@router.get("/specialties")
def list_specialties(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(Specialty).where(Specialty.is_active.is_(True))).scalars()
    return [
        {
            "code": s.code,
            "name": s.name,
            "name_si": s.name_si,
            "name_ta": s.name_ta,
            "description": s.description,
            "sub_specialties": s.sub_specialties or [],
        }
        for s in rows
    ]


@router.get("/capabilities")
def list_capabilities() -> list[dict]:
    return [
        {"code": c.code, "name": c.name, "category": c.category}
        for c in CAPABILITY_BY_CODE.values()
    ]


@router.get("/tests")
def list_tests(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(DiagnosticTest).where(DiagnosticTest.is_active.is_(True))
    ).scalars()
    return [
        {
            "code": t.code,
            "name": t.name,
            "category": t.category,
            "description": t.description,
            "required_capability": t.required_capability,
            "typical_price_lkr": t.typical_price_lkr,
            "preparation_notes": t.preparation_notes,
        }
        for t in rows
    ]
