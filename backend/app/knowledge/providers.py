"""The provider directory, generated from the database — never hardcoded.

A patient asks "who can do an MRI in Kandy on a Saturday?" That question is
not answerable by keyword-filtering the doctors table, and it is not
answerable by a hand-written FAQ either. It needs the directory to exist as
retrievable *text*.

So every doctor, hospital and test offering is rendered into a natural-language
passage at ingest time, straight from the live rows. Add a doctor through the
admin UI, re-run ingestion, and they are searchable — there is no second copy
of the directory to keep in sync, because this file writes prose and owns no
facts of its own.

**What is deliberately left out.** These passages are embedded into a vector
store and retrieved into model prompts, so they carry only what is already
public on a listing page: name, specialty, hospital, city, languages, fees,
clinic days. No patient ever appears here. Doctors' personal contact details
and registration numbers are excluded too — a directory entry should help
someone choose a clinician, not become a scrape target.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.providers import (
    Doctor,
    FacilityTestOffering,
    Hospital,
)

logger = logging.getLogger(__name__)

_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass
class ProviderDoc:
    """One retrievable passage about a real provider row."""

    id: str
    title: str
    topic: str            # doctor | hospital | test
    text: str
    source: str
    # Kept out of the embedded text; used by the UI to render a real card and
    # to deep-link into booking.
    payload: dict[str, Any] = field(default_factory=dict)


def _join(items: list[str], conjunction: str = "and") -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"


def _schedule_phrase(doctor: Doctor) -> str:
    days = sorted({s.day_of_week for s in doctor.schedules if s.is_active})
    if not days:
        return "Clinic days are not published."
    named = [_DAYS[d] for d in days if 0 <= d < 7]
    times = sorted(
        {
            f"{s.start_time.strftime('%I:%M %p').lstrip('0')}"
            for s in doctor.schedules
            if s.is_active
        }
    )
    phrase = f"Consults on {_join(named)}"
    if times:
        phrase += f", typically from {times[0]}"
    return phrase + "."


def doctor_docs(db: Session) -> list[ProviderDoc]:
    doctors = db.execute(
        select(Doctor)
        .options(
            selectinload(Doctor.user),
            selectinload(Doctor.specialty),
            selectinload(Doctor.hospital),
            selectinload(Doctor.schedules),
        )
        .where(Doctor.is_active.is_(True))
    ).scalars()

    docs: list[ProviderDoc] = []
    for doctor in doctors:
        if doctor.user is None or doctor.specialty is None:
            continue

        name = doctor.user.full_name
        specialty = doctor.specialty.name
        hospital = doctor.hospital.name if doctor.hospital else "an independent practice"
        city = doctor.hospital.city if doctor.hospital else ""

        sentences = [
            f"{name} is a {specialty.lower()} specialist practising at {hospital}"
            + (f" in {city}." if city else "."),
        ]

        if doctor.sub_specialty:
            sentences.append(f"Their special interest is {doctor.sub_specialty}.")

        if doctor.years_experience:
            sentences.append(
                f"They have {doctor.years_experience} years of experience"
                + (
                    f" and hold {_join(list(doctor.qualifications))}."
                    if doctor.qualifications
                    else "."
                )
            )

        languages = [str(lang) for lang in (doctor.languages or [])]
        if languages:
            sentences.append(f"Consultations are available in {_join(languages)}.")

        modes = []
        if doctor.supports_physical:
            modes.append("in person")
        if doctor.supports_teleconsultation:
            modes.append("by teleconsultation")
        if modes:
            fee = f"LKR {doctor.consultation_fee_lkr:,.0f}"
            sentences.append(
                f"They can be seen {_join(modes, 'or')}. The consultation fee is {fee}."
            )

        sentences.append(_schedule_phrase(doctor))
        sentences.append(
            "They are accepting new patients."
            if doctor.accepts_new_patients
            else "They are not currently accepting new patients."
        )

        # The specialty's own keywords are appended so a patient searching by
        # symptom ("skin rash") reaches the dermatologist without having to
        # know the word "dermatology".
        keywords = [str(k) for k in (doctor.specialty.keywords or [])]
        if keywords:
            sentences.append(
                f"Patients usually see this specialty for {_join(keywords[:8])}."
            )

        docs.append(ProviderDoc(
            id=f"pd-doc-{doctor.id}",
            title=f"{name} — {specialty}",
            topic="doctor",
            text=" ".join(sentences),
            source="SuwaPath verified provider directory",
            payload={
                "kind": "doctor",
                "doctor_id": doctor.id,
                "name": name,
                "specialty": specialty,
                "specialty_code": doctor.specialty.code,
                "sub_specialty": doctor.sub_specialty,
                "hospital_id": doctor.hospital_id,
                "hospital_name": doctor.hospital.name if doctor.hospital else None,
                "city": city,
                "languages": languages,
                "fee_lkr": doctor.consultation_fee_lkr,
                "rating": doctor.rating,
                "years_experience": doctor.years_experience,
                "teleconsultation": doctor.supports_teleconsultation,
                "accepts_new_patients": doctor.accepts_new_patients,
            },
        ))

    return docs


def hospital_docs(db: Session) -> list[ProviderDoc]:
    hospitals = db.execute(
        select(Hospital)
        .options(selectinload(Hospital.capabilities), selectinload(Hospital.doctors))
        .where(Hospital.is_active.is_(True))
    ).scalars()

    docs: list[ProviderDoc] = []
    for hospital in hospitals:
        facility_type = str(hospital.facility_type).replace("_", " ")
        sentences = [
            f"{hospital.name} is a {facility_type} in {hospital.city}, "
            f"{hospital.district} district.",
        ]

        if hospital.is_24_hours:
            sentences.append("It is open 24 hours.")
        elif hospital.opening_time and hospital.closing_time:
            sentences.append(
                f"It is open from "
                f"{hospital.opening_time.strftime('%I:%M %p').lstrip('0')} to "
                f"{hospital.closing_time.strftime('%I:%M %p').lstrip('0')}."
            )

        sentences.append(
            "It has an emergency department."
            if hospital.has_emergency
            else "It does not have an emergency department."
        )

        available = [
            c.capability_name for c in hospital.capabilities if c.is_available
        ]
        if available:
            sentences.append(f"Services available here include {_join(available[:14])}.")

        if hospital.bed_count:
            beds = f"{hospital.bed_count} beds"
            if hospital.icu_bed_count:
                beds += f", including {hospital.icu_bed_count} ICU beds"
            sentences.append(f"The facility has {beds}.")

        specialties = sorted({
            d.specialty.name for d in hospital.doctors
            if d.is_active and d.specialty is not None
        })
        if specialties:
            sentences.append(f"Specialties practising here: {_join(specialties[:12])}.")

        docs.append(ProviderDoc(
            id=f"pd-hos-{hospital.id}",
            title=f"{hospital.name} — {hospital.city}",
            topic="hospital",
            text=" ".join(sentences),
            source="SuwaPath verified facility directory",
            payload={
                "kind": "hospital",
                "hospital_id": hospital.id,
                "name": hospital.name,
                "facility_type": str(hospital.facility_type),
                "city": hospital.city,
                "district": hospital.district,
                "address": hospital.address,
                "has_emergency": hospital.has_emergency,
                "is_24_hours": hospital.is_24_hours,
                "rating": hospital.rating,
                "capabilities": available[:20],
            },
        ))

    return docs


def test_docs(db: Session) -> list[ProviderDoc]:
    """Where each diagnostic test can actually be done, and for how much.

    One passage per test rather than per offering: a patient asks "where can I
    get an MRI", not "tell me about MRI at facility 7".
    """
    offerings = db.execute(
        select(FacilityTestOffering)
        .options(
            selectinload(FacilityTestOffering.test),
            selectinload(FacilityTestOffering.hospital),
        )
        .where(FacilityTestOffering.is_available.is_(True))
    ).scalars()

    by_test: dict[str, list[FacilityTestOffering]] = {}
    for offering in offerings:
        if offering.test is None or offering.hospital is None:
            continue
        by_test.setdefault(offering.test.id, []).append(offering)

    docs: list[ProviderDoc] = []
    for offerings_for_test in by_test.values():
        test = offerings_for_test[0].test
        cheapest = sorted(offerings_for_test, key=lambda o: o.price_lkr)

        sentences = [f"{test.name} is a {test.category} test."]
        if test.description:
            sentences.append(test.description)
        if test.preparation_notes:
            sentences.append(f"Preparation: {test.preparation_notes}")

        places = [
            f"{o.hospital.name} in {o.hospital.city} (LKR {o.price_lkr:,.0f}, "
            f"results in about {o.turnaround_hours} hours)"
            for o in cheapest[:6]
        ]
        sentences.append(f"It is available at {_join(places)}.")

        prices = [o.price_lkr for o in offerings_for_test]
        sentences.append(
            f"Prices range from LKR {min(prices):,.0f} to LKR {max(prices):,.0f}."
        )

        docs.append(ProviderDoc(
            id=f"pd-test-{test.id}",
            title=f"{test.name} — where to get it",
            topic="test",
            text=" ".join(sentences),
            source="SuwaPath diagnostic test directory",
            payload={
                "kind": "test",
                "test_id": test.id,
                "name": test.name,
                "category": test.category,
                "required_capability": test.required_capability,
                "min_price_lkr": min(prices),
                "max_price_lkr": max(prices),
                "offerings": [
                    {
                        "hospital_id": o.hospital_id,
                        "hospital_name": o.hospital.name,
                        "city": o.hospital.city,
                        "price_lkr": o.price_lkr,
                        "turnaround_hours": o.turnaround_hours,
                    }
                    for o in cheapest[:8]
                ],
            },
        ))

    return docs


def build(db: Session) -> list[ProviderDoc]:
    """Every provider passage, regenerated from the current database."""
    docs = doctor_docs(db) + hospital_docs(db) + test_docs(db)
    logger.info("Built %d provider directory documents from the database.", len(docs))
    return docs
