"""Domain enumerations shared by models, schemas and services."""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    PATIENT = "patient"
    GUARDIAN = "guardian"
    DOCTOR = "doctor"
    HOSPITAL_ADMIN = "hospital_admin"
    SYSTEM_ADMIN = "system_admin"


class Language(StrEnum):
    EN = "en"
    SI = "si"
    TA = "ta"


class Sex(StrEnum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class UrgencyLevel(StrEnum):
    """Care-level decision produced by the deterministic red-flag engine."""

    EMERGENCY = "emergency"
    URGENT = "urgent"
    ROUTINE = "routine"
    SELF_CARE = "self_care"

    @property
    def rank(self) -> int:
        return {"self_care": 0, "routine": 1, "urgent": 2, "emergency": 3}[self.value]


class AppointmentStatus(StrEnum):
    AVAILABLE = "available"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    IN_CONSULTATION = "in_consultation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class VisitType(StrEnum):
    PHYSICAL = "physical"
    TELECONSULTATION = "teleconsultation"


class ConsultationStatus(StrEnum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class DocumentType(StrEnum):
    LAB_REPORT = "lab_report"
    PRESCRIPTION = "prescription"
    RADIOLOGY_REPORT = "radiology_report"
    DISCHARGE_SUMMARY = "discharge_summary"
    CLINICAL_REPORT = "clinical_report"
    OTHER = "other"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ImageModality(StrEnum):
    CHEST_XRAY = "chest_xray"
    SKIN_LESION = "skin_lesion"
    FUNDUS = "fundus"
    OTHER = "other"


class ResultFlag(StrEnum):
    """Where a lab value sits against the reference range printed on the report."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class NotificationCategory(StrEnum):
    EMERGENCY = "emergency"
    APPOINTMENT = "appointment"
    MEDICATION = "medication"
    CARE_PROGRAMME = "care_programme"
    REPORT = "report"
    DOCTOR_MESSAGE = "doctor_message"
    HOSPITAL_ALERT = "hospital_alert"
    GUARDIAN_ALERT = "guardian_alert"
    FOLLOW_UP = "follow_up"


class NotificationPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class GuardianPermissionType(StrEnum):
    """Consent scopes a patient can grant a guardian. Default is deny-all."""

    APPOINTMENTS = "appointments"
    MEDICATIONS = "medications"
    REMINDERS = "reminders"
    WELLBEING = "wellbeing"
    CARE_PROGRAMME = "care_programme"
    EMERGENCY_ALERTS = "emergency_alerts"
    REPORTS = "reports"
    FULL_MEDICAL = "full_medical"


class ProgrammeType(StrEnum):
    MATERNAL = "maternal"
    POSTPARTUM = "postpartum"
    ELDERLY = "elderly"
    SEXUAL_HEALTH = "sexual_health"
    CHRONIC = "chronic"


class EnrollmentStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    PAUSED = "paused"
    WITHDRAWN = "withdrawn"


class MedicationLogStatus(StrEnum):
    TAKEN = "taken"
    SKIPPED = "skipped"
    SNOOZED = "snoozed"
    MISSED = "missed"


class WellbeingStatus(StrEnum):
    GOOD = "good"
    NOT_GREAT = "not_great"
    NEED_HELP = "need_help"


class CheckInType(StrEnum):
    MATERNAL = "maternal"
    POSTPARTUM = "postpartum"
    ELDERLY = "elderly"


class RiskBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReferralStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    DECLINED = "declined"
    EXPIRED = "expired"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    PENDING = "pending"
    REJECTED = "rejected"


class FacilityType(StrEnum):
    HOSPITAL = "hospital"
    DIAGNOSTIC_CENTRE = "diagnostic_centre"
    CLINIC = "clinic"


class AlertSeverity(StrEnum):
    INFO = "info"
    ATTENTION = "attention"
    CRITICAL = "critical"
