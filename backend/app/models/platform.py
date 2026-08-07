"""Notifications, messaging, guardian alerts, anonymous sessions and analytics.

`AnonymousHealthSession` is intentionally free of foreign keys to `users`: a
confidential sexual-health session must stay separated from normal patient
history (internal rule 7) unless the user explicitly links it.
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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid
from app.models.enums import (
    AlertSeverity,
    Language,
    NotificationCategory,
    NotificationPriority,
    RiskBand,
    SessionStatus,
)

if TYPE_CHECKING:
    from app.models.identity import User


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notif_user_read", "user_id", "is_read"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    category: Mapped[NotificationCategory] = mapped_column(String(32), index=True)
    priority: Mapped[NotificationPriority] = mapped_column(
        String(16), default=NotificationPriority.NORMAL, index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)

    # Deep-link target, e.g. {"type": "appointment", "id": "..."}
    action_type: Mapped[str | None] = mapped_column(String(48))
    action_id: Mapped[str | None] = mapped_column(String(36))

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set for reminders that should surface at a future time.
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    # For guardian alerts: which dependent this concerns.
    about_patient_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class Message(Base, TimestampMixin):
    """Care-team messaging between a patient/guardian and a doctor."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_message_thread_created", "thread_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), index=True)
    sender_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    recipient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The patient the thread is about (may differ from sender for guardians).
    subject_patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    body: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attachment_ids: Mapped[list] = mapped_column(JSONB, default=list)


class GuardianAlert(Base, TimestampMixin):
    """A pattern-based alert raised to a guardian, subject to consent scopes."""

    __tablename__ = "guardian_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    guardian_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    alert_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[AlertSeverity] = mapped_column(
        String(24), default=AlertSeverity.ATTENTION, index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text)
    # Which consent scope authorised delivery of this alert.
    required_permission: Mapped[str] = mapped_column(String(32))

    is_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class HospitalAlert(Base, TimestampMixin):
    """Operational alert on the hospital administrator dashboard."""

    __tablename__ = "hospital_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    hospital_id: Mapped[str] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), index=True
    )
    alert_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[AlertSeverity] = mapped_column(
        String(24), default=AlertSeverity.ATTENTION
    )
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class AnonymousHealthSession(Base, TimestampMixin):
    """A pseudonymous confidential session, resumable by recovery code.

    Deliberately stores no name, email, phone or user foreign key.
    """

    __tablename__ = "anonymous_health_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # Shown to the user once; only its hash is persisted.
    recovery_code_hash: Mapped[str] = mapped_column(String(255), index=True)
    display_alias: Mapped[str] = mapped_column(String(48), default="Private session")

    language: Mapped[Language] = mapped_column(String(8), default=Language.EN)
    status: Mapped[SessionStatus] = mapped_column(String(24), default=SessionStatus.ACTIVE)

    # Coarse, non-identifying context used only for facility matching.
    approximate_city: Mapped[str | None] = mapped_column(String(80))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    age_band: Mapped[str | None] = mapped_column(String(24))

    # Structured confidential intake (exposure type, protection, testing history).
    structured_answers: Mapped[dict] = mapped_column(JSONB, default=dict)
    testing_guidance: Mapped[str | None] = mapped_column(Text)
    suggested_specialty_code: Mapped[str | None] = mapped_column(String(64))
    recommended_tests: Mapped[list] = mapped_column(JSONB, default=list)

    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NoShowPrediction(Base, TimestampMixin):
    """Predicted no-show risk for a future appointment."""

    __tablename__ = "no_show_predictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    appointment_id: Mapped[str] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"), unique=True, index=True
    )
    hospital_id: Mapped[str | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), index=True
    )

    probability: Mapped[float] = mapped_column(Float, index=True)
    risk_band: Mapped[RiskBand] = mapped_column(String(16), index=True)
    # Per-feature contributions so staff can see *why* it is high risk.
    contributing_factors: Mapped[list] = mapped_column(JSONB, default=list)
    model_version: Mapped[str] = mapped_column(String(24), default="1.0.0")
    predicted_for_date: Mapped[date] = mapped_column(Date, index=True)


class Forecast(Base, TimestampMixin):
    """Next-7-day specialty demand forecast against available capacity."""

    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint(
            "hospital_id", "specialty_code", "forecast_date", name="uq_forecast_slot"
        ),
        Index("ix_forecast_hospital_date", "hospital_id", "forecast_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    hospital_id: Mapped[str] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), index=True
    )
    specialty_code: Mapped[str] = mapped_column(String(64), index=True)
    forecast_date: Mapped[date] = mapped_column(Date, index=True)

    predicted_demand: Mapped[float] = mapped_column(Float)
    lower_bound: Mapped[float] = mapped_column(Float)
    upper_bound: Mapped[float] = mapped_column(Float)
    available_capacity: Mapped[int] = mapped_column(Integer)
    already_booked: Mapped[int] = mapped_column(Integer, default=0)

    # True when predicted demand exceeds capacity — drives the warning banner.
    capacity_warning: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    utilisation_percent: Mapped[float] = mapped_column(Float, default=0.0)
    model_version: Mapped[str] = mapped_column(String(24), default="1.0.0")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
