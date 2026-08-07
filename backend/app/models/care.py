"""Appointments, consultations, referrals, medications and care programmes.

These tables are the shared spine of the care journey: the same `Appointment`
row is what the patient books, what appears in the doctor's live queue and what
the hospital administrator counts in operational analytics.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid
from app.models.enums import (
    AppointmentStatus,
    CheckInType,
    ConsultationStatus,
    EnrollmentStatus,
    MedicationLogStatus,
    ProgrammeType,
    ReferralStatus,
    UrgencyLevel,
    VisitType,
    WellbeingStatus,
)

if TYPE_CHECKING:
    from app.models.identity import User
    from app.models.providers import Doctor, Hospital


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appt_doctor_start", "doctor_id", "scheduled_start"),
        Index("ix_appt_hospital_start", "hospital_id", "scheduled_start"),
        Index("ix_appt_patient_start", "patient_user_id", "scheduled_start"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    doctor_id: Mapped[str] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    hospital_id: Mapped[str | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    # Set when a guardian books on a dependent's behalf.
    booked_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Links the booking back to the recommendation that produced it, closing
    # the navigation loop for conversion analytics.
    recommendation_id: Mapped[str | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL"), index=True
    )

    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    visit_type: Mapped[VisitType] = mapped_column(String(24), default=VisitType.PHYSICAL)
    status: Mapped[AppointmentStatus] = mapped_column(
        String(24), default=AppointmentStatus.PENDING, index=True
    )
    urgency: Mapped[UrgencyLevel] = mapped_column(String(24), default=UrgencyLevel.ROUTINE)

    reason: Mapped[str | None] = mapped_column(Text)
    chief_complaint: Mapped[str | None] = mapped_column(Text)
    fee_lkr: Mapped[float | None] = mapped_column(Float)

    # --- Lifecycle timestamps, also used as no-show model features ---
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    reschedule_count: Mapped[int] = mapped_column(Integer, default=0)
    reminder_sent_count: Mapped[int] = mapped_column(Integer, default=0)
    patient_distance_km: Mapped[float | None] = mapped_column(Float)
    teleconsultation_url: Mapped[str | None] = mapped_column(String(512))

    patient: Mapped["User"] = relationship(foreign_keys=[patient_user_id])
    doctor: Mapped["Doctor"] = relationship()
    hospital: Mapped["Hospital | None"] = relationship()
    consultation: Mapped["Consultation | None"] = relationship(
        back_populates="appointment", uselist=False
    )

    @property
    def lead_time_days(self) -> float | None:
        """Days between booking and the appointment — a no-show predictor."""
        if not self.booked_at:
            return None
        return (self.scheduled_start - self.booked_at).total_seconds() / 86400


class Consultation(Base, TimestampMixin):
    """The clinical encounter record written by the doctor."""

    __tablename__ = "consultations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    appointment_id: Mapped[str | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), unique=True, index=True
    )
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    doctor_id: Mapped[str] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    hospital_id: Mapped[str | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL")
    )

    status: Mapped[ConsultationStatus] = mapped_column(
        String(24), default=ConsultationStatus.SCHEDULED, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    presenting_complaint: Mapped[str | None] = mapped_column(Text)
    clinical_notes: Mapped[str | None] = mapped_column(Text)
    assessment: Mapped[str | None] = mapped_column(Text)
    diagnosis_text: Mapped[str | None] = mapped_column(Text)
    treatment_plan: Mapped[str | None] = mapped_column(Text)
    prescribed_medications: Mapped[list] = mapped_column(JSONB, default=list)
    requested_tests: Mapped[list] = mapped_column(JSONB, default=list)

    follow_up_required: Mapped[bool] = mapped_column(Boolean, default=False)
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    follow_up_notes: Mapped[str | None] = mapped_column(Text)

    # Did the clinician agree with the AI-suggested specialty? Feeds the
    # "clinician agreement with care level" impact metric.
    ai_specialty_accepted: Mapped[bool | None] = mapped_column(Boolean)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)

    appointment: Mapped["Appointment | None"] = relationship(back_populates="consultation")
    doctor: Mapped["Doctor"] = relationship()


class Referral(Base, TimestampMixin):
    __tablename__ = "referrals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    from_doctor_id: Mapped[str | None] = mapped_column(
        ForeignKey("doctors.id", ondelete="SET NULL")
    )
    to_doctor_id: Mapped[str | None] = mapped_column(
        ForeignKey("doctors.id", ondelete="SET NULL"), index=True
    )
    to_hospital_id: Mapped[str | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    consultation_id: Mapped[str | None] = mapped_column(
        ForeignKey("consultations.id", ondelete="SET NULL")
    )

    specialty_code: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(Text)
    urgency: Mapped[UrgencyLevel] = mapped_column(String(24), default=UrgencyLevel.ROUTINE)
    status: Mapped[ReferralStatus] = mapped_column(
        String(24), default=ReferralStatus.PENDING, index=True
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Shares the structured intake with the receiving clinician when true.
    shares_intake: Mapped[bool] = mapped_column(Boolean, default=True)


class Medication(Base, TimestampMixin):
    """An active medication schedule for a patient."""

    __tablename__ = "medications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    prescribed_by_doctor_id: Mapped[str | None] = mapped_column(
        ForeignKey("doctors.id", ondelete="SET NULL")
    )

    name: Mapped[str] = mapped_column(String(200))
    dosage: Mapped[str] = mapped_column(String(80))
    form: Mapped[str] = mapped_column(String(48), default="tablet")
    instructions: Mapped[str | None] = mapped_column(Text)
    # Times of day in "HH:MM", e.g. ["08:00", "20:00"]
    schedule_times: Mapped[list] = mapped_column(JSONB, default=list)
    frequency_label: Mapped[str] = mapped_column(String(80), default="Once daily")

    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)

    logs: Mapped[list["MedicationLog"]] = relationship(
        back_populates="medication", cascade="all, delete-orphan"
    )


class MedicationLog(Base, TimestampMixin):
    """One scheduled dose and what the patient did about it."""

    __tablename__ = "medication_logs"
    __table_args__ = (Index("ix_medlog_patient_due", "patient_user_id", "due_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    medication_id: Mapped[str] = mapped_column(
        ForeignKey("medications.id", ondelete="CASCADE"), index=True
    )
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[MedicationLogStatus] = mapped_column(
        String(24), default=MedicationLogStatus.MISSED, index=True
    )
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    medication: Mapped["Medication"] = relationship(back_populates="logs")


class CareProgramme(Base, TimestampMixin):
    """Template for a care pathway, editable by the system administrator."""

    __tablename__ = "care_programmes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    programme_type: Mapped[ProgrammeType] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(Text)
    # Ordered milestones, e.g. antenatal visit schedule.
    milestones: Mapped[list] = mapped_column(JSONB, default=list)
    check_in_questions: Mapped[list] = mapped_column(JSONB, default=list)
    default_reminders: Mapped[list] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CareEnrollment(Base, TimestampMixin):
    __tablename__ = "care_enrollments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    programme_id: Mapped[str] = mapped_column(
        ForeignKey("care_programmes.id", ondelete="CASCADE"), index=True
    )
    hospital_id: Mapped[str | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    primary_doctor_id: Mapped[str | None] = mapped_column(
        ForeignKey("doctors.id", ondelete="SET NULL")
    )

    status: Mapped[EnrollmentStatus] = mapped_column(
        String(24), default=EnrollmentStatus.ACTIVE, index=True
    )
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text)

    programme: Mapped["CareProgramme"] = relationship()


class MaternalRecord(Base, TimestampMixin):
    """Pregnancy/postpartum state backing the maternal dashboard."""

    __tablename__ = "maternal_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    enrollment_id: Mapped[str | None] = mapped_column(
        ForeignKey("care_enrollments.id", ondelete="SET NULL")
    )

    last_menstrual_period: Mapped[date | None] = mapped_column(Date)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date)
    is_postpartum: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_date: Mapped[date | None] = mapped_column(Date)
    delivery_type: Mapped[str | None] = mapped_column(String(48))

    gravida: Mapped[int] = mapped_column(Integer, default=1)
    para: Mapped[int] = mapped_column(Integer, default=0)
    previous_pregnancies: Mapped[list] = mapped_column(JSONB, default=list)
    risk_conditions: Mapped[list] = mapped_column(JSONB, default=list)
    is_high_risk: Mapped[bool] = mapped_column(Boolean, default=False)

    blood_group: Mapped[str | None] = mapped_column(String(8))
    baseline_weight_kg: Mapped[float | None] = mapped_column(Float)
    current_weight_kg: Mapped[float | None] = mapped_column(Float)

    # Newborn tracking once postpartum.
    newborn_name: Mapped[str | None] = mapped_column(String(160))
    newborn_dob: Mapped[date | None] = mapped_column(Date)
    newborn_birth_weight_kg: Mapped[float | None] = mapped_column(Float)
    feeding_method: Mapped[str | None] = mapped_column(String(48))
    epds_score: Mapped[int | None] = mapped_column(Integer)  # postpartum mood screen
    epds_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def pregnancy_week(self) -> int | None:
        """Gestational age in completed weeks, derived from EDD (280-day term)."""
        if self.is_postpartum or not self.expected_delivery_date:
            return None
        days_remaining = (self.expected_delivery_date - date.today()).days
        week = (280 - days_remaining) // 7
        return max(0, min(42, week))


class ElderlyRecord(Base, TimestampMixin):
    """Elderly-pathway state and the guardian alert thresholds."""

    __tablename__ = "elderly_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    enrollment_id: Mapped[str | None] = mapped_column(
        ForeignKey("care_enrollments.id", ondelete="SET NULL")
    )

    living_situation: Mapped[str | None] = mapped_column(String(80))
    mobility_level: Mapped[str] = mapped_column(String(48), default="independent")
    uses_walking_aid: Mapped[bool] = mapped_column(Boolean, default=False)
    fall_risk_level: Mapped[str] = mapped_column(String(24), default="low")
    chronic_conditions: Mapped[list] = mapped_column(JSONB, default=list)

    # Guardian alerting thresholds — deliberately not per-event (spec §14).
    missed_medication_alert_threshold: Mapped[int] = mapped_column(Integer, default=2)
    missed_checkin_alert_threshold: Mapped[int] = mapped_column(Integer, default=2)

    last_check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_missed_checkins: Mapped[int] = mapped_column(Integer, default=0)


class DailyCheckIn(Base, TimestampMixin):
    """A maternal, postpartum or elderly check-in submission."""

    __tablename__ = "daily_check_ins"
    __table_args__ = (Index("ix_checkin_patient_date", "patient_user_id", "check_in_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    enrollment_id: Mapped[str | None] = mapped_column(
        ForeignKey("care_enrollments.id", ondelete="SET NULL"), index=True
    )

    check_in_type: Mapped[CheckInType] = mapped_column(String(24), index=True)
    check_in_date: Mapped[date] = mapped_column(Date, index=True)
    wellbeing: Mapped[WellbeingStatus | None] = mapped_column(String(24))

    # Question code -> answer, e.g. {"severe_headache": true}
    responses: Mapped[dict] = mapped_column(JSONB, default=dict)
    danger_signs_reported: Mapped[list] = mapped_column(JSONB, default=list)
    triggered_alert: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    escalation_message: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class VitalRecord(Base, TimestampMixin):
    """Patient- or clinician-entered vital sign."""

    __tablename__ = "vital_records"
    __table_args__ = (Index("ix_vital_patient_type", "patient_user_id", "vital_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    vital_type: Mapped[str] = mapped_column(String(48))  # blood_pressure, glucose, ...
    value_numeric: Mapped[float | None] = mapped_column(Float)
    systolic: Mapped[int | None] = mapped_column(Integer)
    diastolic: Mapped[int | None] = mapped_column(Integer)
    unit: Mapped[str | None] = mapped_column(String(32))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_abnormal: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source: Mapped[str] = mapped_column(String(32), default="patient")
    notes: Mapped[str | None] = mapped_column(Text)
