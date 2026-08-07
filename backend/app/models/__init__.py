"""SQLAlchemy models for SuwaPath.

Importing this package registers every mapper, which `create_all()` relies on.
"""

from app.models.base import Base, TimestampMixin, new_uuid, utcnow
from app.models.care import (
    Appointment,
    CareEnrollment,
    CareProgramme,
    Consultation,
    DailyCheckIn,
    ElderlyRecord,
    MaternalRecord,
    Medication,
    MedicationLog,
    Referral,
    VitalRecord,
)
from app.models.clinical import (
    ExtractedReport,
    ExtractedValue,
    ImageAnalysis,
    MedicalDocument,
    MedicalImage,
    Recommendation,
    RedFlagAssessment,
    StructuredIntake,
    SymptomMessage,
    SymptomSession,
)
from app.models.identity import (
    AuditLog,
    GuardianPermission,
    GuardianRelationship,
    PatientProfile,
    SystemConfig,
    User,
)
from app.models.platform import (
    AnonymousHealthSession,
    Forecast,
    GuardianAlert,
    HospitalAlert,
    Message,
    NoShowPrediction,
    Notification,
)
from app.models.providers import (
    DiagnosticTest,
    Doctor,
    DoctorSchedule,
    FacilityCapability,
    FacilityTestOffering,
    Hospital,
    Specialty,
)

__all__ = [
    "Base",
    "TimestampMixin",
    "new_uuid",
    "utcnow",
    # identity
    "User",
    "PatientProfile",
    "GuardianRelationship",
    "GuardianPermission",
    "AuditLog",
    "SystemConfig",
    # providers
    "Specialty",
    "Hospital",
    "FacilityCapability",
    "DiagnosticTest",
    "FacilityTestOffering",
    "Doctor",
    "DoctorSchedule",
    # clinical
    "SymptomSession",
    "SymptomMessage",
    "StructuredIntake",
    "RedFlagAssessment",
    "Recommendation",
    "MedicalDocument",
    "ExtractedReport",
    "ExtractedValue",
    "MedicalImage",
    "ImageAnalysis",
    # care
    "Appointment",
    "Consultation",
    "Referral",
    "Medication",
    "MedicationLog",
    "CareProgramme",
    "CareEnrollment",
    "MaternalRecord",
    "ElderlyRecord",
    "DailyCheckIn",
    "VitalRecord",
    # platform
    "Notification",
    "Message",
    "GuardianAlert",
    "HospitalAlert",
    "AnonymousHealthSession",
    "NoShowPrediction",
    "Forecast",
]
