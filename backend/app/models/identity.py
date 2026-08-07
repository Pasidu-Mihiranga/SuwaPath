"""Users, patient profiles and the guardian consent model.

Guardian access is deny-by-default: a `GuardianRelationship` grants nothing on
its own.  Every scope must be explicitly granted as a `GuardianPermission` row
by the patient (internal rule 6).
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
from app.models.enums import GuardianPermissionType, Language, Sex, UserRole

if TYPE_CHECKING:
    from app.models.providers import Doctor, Hospital


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))

    full_name: Mapped[str] = mapped_column(String(160))
    role: Mapped[UserRole] = mapped_column(String(32), index=True)
    preferred_language: Mapped[Language] = mapped_column(String(8), default=Language.EN)
    avatar_url: Mapped[str | None] = mapped_column(String(512))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Set for hospital_admin users; scopes their operational dashboard.
    hospital_id: Mapped[str | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )

    patient_profile: Mapped["PatientProfile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    doctor_profile: Mapped["Doctor | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    hospital: Mapped["Hospital | None"] = relationship(foreign_keys=[hospital_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email} ({self.role})>"


class PatientProfile(Base, TimestampMixin):
    __tablename__ = "patient_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )

    date_of_birth: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[Sex | None] = mapped_column(String(16))
    blood_group: Mapped[str | None] = mapped_column(String(8))

    # Location drives distance-based provider matching.
    city: Mapped[str | None] = mapped_column(String(80), index=True)
    district: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    height_cm: Mapped[float | None] = mapped_column(Float)
    weight_kg: Mapped[float | None] = mapped_column(Float)

    # Clinical background surfaced in every pre-consultation summary.
    chronic_conditions: Mapped[list] = mapped_column(JSONB, default=list)
    allergies: Mapped[list] = mapped_column(JSONB, default=list)
    current_medications: Mapped[list] = mapped_column(JSONB, default=list)
    past_surgeries: Mapped[list] = mapped_column(JSONB, default=list)
    family_history: Mapped[list] = mapped_column(JSONB, default=list)

    # Maternal pathway fields (mirrored in MaternalRecord once enrolled).
    is_pregnant: Mapped[bool] = mapped_column(Boolean, default=False)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date)

    emergency_contact_name: Mapped[str | None] = mapped_column(String(160))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(32))

    # Accessibility: elderly pathway renders large controls when true.
    accessibility_large_text: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="patient_profile")

    @property
    def age(self) -> int | None:
        if not self.date_of_birth:
            return None
        today = date.today()
        return (
            today.year
            - self.date_of_birth.year
            - ((today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
        )


class GuardianRelationship(Base, TimestampMixin):
    """Links a guardian user to a dependent patient. Grants no data access alone."""

    __tablename__ = "guardian_relationships"
    __table_args__ = (
        UniqueConstraint("guardian_user_id", "patient_user_id", name="uq_guardian_patient"),
        Index("ix_guardian_rel_patient", "patient_user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    guardian_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    patient_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    relationship_label: Mapped[str] = mapped_column(String(64))  # e.g. "Father", "Wife"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # A guardian may book on the dependent's behalf only when this is true.
    can_book_appointments: Mapped[bool] = mapped_column(Boolean, default=False)

    guardian: Mapped["User"] = relationship(foreign_keys=[guardian_user_id])
    patient: Mapped["User"] = relationship(foreign_keys=[patient_user_id])
    permissions: Mapped[list["GuardianPermission"]] = relationship(
        back_populates="relationship_ref", cascade="all, delete-orphan"
    )

    def granted_scopes(self) -> set[GuardianPermissionType]:
        return {p.permission for p in self.permissions if p.granted}


class GuardianPermission(Base, TimestampMixin):
    """One consent scope, explicitly granted (or revoked) by the patient."""

    __tablename__ = "guardian_permissions"
    __table_args__ = (
        UniqueConstraint("relationship_id", "permission", name="uq_relationship_permission"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    relationship_id: Mapped[str] = mapped_column(
        ForeignKey("guardian_relationships.id", ondelete="CASCADE"), index=True
    )
    permission: Mapped[GuardianPermissionType] = mapped_column(String(32))
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    relationship_ref: Mapped["GuardianRelationship"] = relationship(
        back_populates="permissions"
    )


class AuditLog(Base, TimestampMixin):
    """System-level audit trail surfaced to the SuwaPath system administrator."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    actor_role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))


class SystemConfig(Base, TimestampMixin):
    """Key/value configuration editable by the system administrator."""

    __tablename__ = "system_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64), default="general")
