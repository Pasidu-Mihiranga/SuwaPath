"""Specialties, facilities, capabilities, diagnostic tests and doctors.

`FacilityCapability` is what makes SuwaPath's matching *capability-aware*: a
recommendation can require "dermatology consult + skin biopsy", and the matcher
will only surface facilities that actually provide both.
"""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid
from app.models.enums import FacilityType, VerificationStatus

if TYPE_CHECKING:
    from app.models.identity import User


class Specialty(Base, TimestampMixin):
    __tablename__ = "specialties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    name_si: Mapped[str | None] = mapped_column(String(160))
    name_ta: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    # Free-text keywords the navigation engine maps symptoms against.
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    sub_specialties: Mapped[list] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Hospital(Base, TimestampMixin):
    """A hospital, clinic or standalone diagnostic centre."""

    __tablename__ = "hospitals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), index=True)
    facility_type: Mapped[FacilityType] = mapped_column(
        String(32), default=FacilityType.HOSPITAL, index=True
    )
    registration_no: Mapped[str | None] = mapped_column(String(64))

    city: Mapped[str] = mapped_column(String(80), index=True)
    district: Mapped[str] = mapped_column(String(80), index=True)
    address: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)

    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(160))

    opening_time: Mapped[time | None] = mapped_column(Time)
    closing_time: Mapped[time | None] = mapped_column(Time)
    is_24_hours: Mapped[bool] = mapped_column(Boolean, default=False)
    has_emergency: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Operational capacity, used by the hospital-intelligence dashboard.
    bed_count: Mapped[int] = mapped_column(Integer, default=0)
    icu_bed_count: Mapped[int] = mapped_column(Integer, default=0)
    consultation_rooms: Mapped[int] = mapped_column(Integer, default=0)

    verification_status: Mapped[VerificationStatus] = mapped_column(
        String(24), default=VerificationStatus.VERIFIED
    )
    rating: Mapped[float] = mapped_column(Float, default=4.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    capabilities: Mapped[list["FacilityCapability"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    doctors: Mapped[list["Doctor"]] = relationship(back_populates="hospital")

    def capability_codes(self) -> set[str]:
        return {c.capability_code for c in self.capabilities if c.is_available}


class FacilityCapability(Base, TimestampMixin):
    """A service a facility can actually deliver (emergency, MRI, biopsy, ...)."""

    __tablename__ = "facility_capabilities"
    __table_args__ = (
        UniqueConstraint("hospital_id", "capability_code", name="uq_facility_capability"),
        Index("ix_capability_code", "capability_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    hospital_id: Mapped[str] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), index=True
    )
    capability_code: Mapped[str] = mapped_column(String(64))
    capability_name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(64), default="clinical")
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    hospital: Mapped["Hospital"] = relationship(back_populates="capabilities")


class DiagnosticTest(Base, TimestampMixin):
    """Catalogue entry for a test/service, optionally priced per facility."""

    __tablename__ = "diagnostic_tests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80), default="laboratory")
    description: Mapped[str | None] = mapped_column(Text)
    # Capability a facility must hold to run this test.
    required_capability: Mapped[str | None] = mapped_column(String(64), index=True)
    typical_price_lkr: Mapped[float | None] = mapped_column(Float)
    preparation_notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FacilityTestOffering(Base, TimestampMixin):
    """Join of facility -> test with local price and turnaround."""

    __tablename__ = "facility_test_offerings"
    __table_args__ = (
        UniqueConstraint("hospital_id", "test_id", name="uq_facility_test"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    hospital_id: Mapped[str] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), index=True
    )
    test_id: Mapped[str] = mapped_column(
        ForeignKey("diagnostic_tests.id", ondelete="CASCADE"), index=True
    )
    price_lkr: Mapped[float] = mapped_column(Float)
    turnaround_hours: Mapped[int] = mapped_column(Integer, default=24)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    hospital: Mapped["Hospital"] = relationship()
    test: Mapped["DiagnosticTest"] = relationship()


class Doctor(Base, TimestampMixin):
    __tablename__ = "doctors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    hospital_id: Mapped[str | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    specialty_id: Mapped[str] = mapped_column(
        ForeignKey("specialties.id", ondelete="RESTRICT"), index=True
    )

    slmc_registration_no: Mapped[str | None] = mapped_column(String(64))
    sub_specialty: Mapped[str | None] = mapped_column(String(120), index=True)
    qualifications: Mapped[list] = mapped_column(JSONB, default=list)
    years_experience: Mapped[int] = mapped_column(Integer, default=0)
    # Languages the doctor consults in; matched against patient preference.
    languages: Mapped[list] = mapped_column(JSONB, default=list)
    bio: Mapped[str | None] = mapped_column(Text)

    supports_teleconsultation: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_physical: Mapped[bool] = mapped_column(Boolean, default=True)
    consultation_fee_lkr: Mapped[float] = mapped_column(Float, default=3000.0)
    teleconsultation_fee_lkr: Mapped[float | None] = mapped_column(Float)

    verification_status: Mapped[VerificationStatus] = mapped_column(
        String(24), default=VerificationStatus.VERIFIED, index=True
    )
    rating: Mapped[float] = mapped_column(Float, default=4.2)
    total_consultations: Mapped[int] = mapped_column(Integer, default=0)
    accepts_new_patients: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="doctor_profile")
    hospital: Mapped["Hospital | None"] = relationship(back_populates="doctors")
    specialty: Mapped["Specialty"] = relationship()
    schedules: Mapped[list["DoctorSchedule"]] = relationship(
        back_populates="doctor", cascade="all, delete-orphan"
    )


class DoctorSchedule(Base, TimestampMixin):
    """Recurring weekly availability block generating bookable slots."""

    __tablename__ = "doctor_schedules"
    __table_args__ = (Index("ix_schedule_doctor_day", "doctor_id", "day_of_week"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    doctor_id: Mapped[str] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    hospital_id: Mapped[str | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL")
    )

    day_of_week: Mapped[int] = mapped_column(Integer)  # 0 = Monday
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    slot_duration_minutes: Mapped[int] = mapped_column(Integer, default=20)
    visit_type: Mapped[str] = mapped_column(String(24), default="physical")
    max_patients: Mapped[int] = mapped_column(Integer, default=20)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    doctor: Mapped["Doctor"] = relationship(back_populates="schedules")
    hospital: Mapped["Hospital | None"] = relationship()
