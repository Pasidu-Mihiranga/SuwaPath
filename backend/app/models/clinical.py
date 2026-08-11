"""The intake pipeline: conversation -> structured intake -> red flags ->
recommendation, plus document and image intelligence.

Design rule: the raw patient conversation (`SymptomMessage`) is stored
*separately* from the AI-derived `StructuredIntake`, and neither is ever
overwritten by the other.  Clinicians always retain access to what the patient
actually said (spec §10).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
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
    DocumentType,
    ImageModality,
    Language,
    ProcessingStatus,
    ResultFlag,
    SessionStatus,
    UrgencyLevel,
)

if TYPE_CHECKING:
    from app.models.identity import User


class SymptomSession(Base, TimestampMixin):
    """One symptom-check conversation."""

    __tablename__ = "symptom_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Set instead of patient_user_id for confidential sexual-health sessions,
    # which must never join back to the normal patient record (rule 7).
    anonymous_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("anonymous_health_sessions.id", ondelete="CASCADE"), index=True
    )

    language: Mapped[Language] = mapped_column(String(8), default=Language.EN)
    status: Mapped[SessionStatus] = mapped_column(String(24), default=SessionStatus.ACTIVE)
    # How many follow-up questions the assistant has asked so far.
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    is_confidential: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list["SymptomMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SymptomMessage.sequence",
    )
    intake: Mapped["StructuredIntake | None"] = relationship(
        back_populates="session", uselist=False, cascade="all, delete-orphan"
    )


class SymptomMessage(Base, TimestampMixin):
    """A single raw conversation turn. Never modified by AI post-processing."""

    __tablename__ = "symptom_messages"
    __table_args__ = (Index("ix_message_session_seq", "session_id", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("symptom_sessions.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # "patient" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    # Which orchestrator capability produced an assistant turn.
    capability: Mapped[str | None] = mapped_column(String(48))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)

    session: Mapped["SymptomSession"] = relationship(back_populates="messages")


class StructuredIntake(Base, TimestampMixin):
    """AI-extracted structure derived from the conversation."""

    __tablename__ = "structured_intakes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("symptom_sessions.id", ondelete="CASCADE"), unique=True, index=True
    )
    patient_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    chief_complaint: Mapped[str] = mapped_column(Text)
    symptoms: Mapped[list] = mapped_column(JSONB, default=list)
    duration_text: Mapped[str | None] = mapped_column(String(120))
    duration_hours: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[int | None] = mapped_column(Integer)  # 1-10 as reported
    associated_symptoms: Mapped[list] = mapped_column(JSONB, default=list)
    relevant_history: Mapped[list] = mapped_column(JSONB, default=list)
    medications: Mapped[list] = mapped_column(JSONB, default=list)
    allergies: Mapped[list] = mapped_column(JSONB, default=list)
    # Candidate flags noted by the extractor; the deterministic engine decides.
    potential_red_flags: Mapped[list] = mapped_column(JSONB, default=list)

    onset: Mapped[str | None] = mapped_column(String(120))
    aggravating_factors: Mapped[list] = mapped_column(JSONB, default=list)
    relieving_factors: Mapped[list] = mapped_column(JSONB, default=list)
    negative_findings: Mapped[list] = mapped_column(JSONB, default=list)

    extraction_source: Mapped[str] = mapped_column(String(32), default="rule_based")
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.6)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)

    session: Mapped["SymptomSession"] = relationship(back_populates="intake")
    red_flag_assessment: Mapped["RedFlagAssessment | None"] = relationship(
        back_populates="intake", uselist=False, cascade="all, delete-orphan"
    )


class RedFlagAssessment(Base, TimestampMixin):
    """Output of the deterministic clinical rule layer.

    The LLM never sets this directly (internal rule 1). Red flags outrank every
    ranking score downstream (internal rule 2).
    """

    __tablename__ = "red_flag_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    intake_id: Mapped[str] = mapped_column(
        ForeignKey("structured_intakes.id", ondelete="CASCADE"), unique=True, index=True
    )

    urgency: Mapped[UrgencyLevel] = mapped_column(String(24), index=True)
    # Each entry: {rule_id, label, matched_terms, severity, rationale}
    triggered_rules: Mapped[list] = mapped_column(JSONB, default=list)
    # Human-readable escalation instruction shown prominently in the UI.
    escalation_message: Mapped[str] = mapped_column(Text)
    requires_emergency_facility: Mapped[bool] = mapped_column(Boolean, default=False)
    rule_engine_version: Mapped[str] = mapped_column(String(24), default="1.0.0")

    intake: Mapped["StructuredIntake"] = relationship(back_populates="red_flag_assessment")


class Recommendation(Base, TimestampMixin):
    """Explainable care-navigation output.

    Produced by symptom intake, report extraction *or* image analysis — every
    one of those paths must feed care navigation (internal rules 4 and 5).
    """

    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    anonymous_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("anonymous_health_sessions.id", ondelete="CASCADE"), index=True
    )

    # Exactly one of these three is set, identifying what triggered navigation.
    intake_id: Mapped[str | None] = mapped_column(
        ForeignKey("structured_intakes.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("medical_documents.id", ondelete="CASCADE"), index=True
    )
    image_id: Mapped[str | None] = mapped_column(
        ForeignKey("medical_images.id", ondelete="CASCADE"), index=True
    )

    source: Mapped[str] = mapped_column(String(32), default="symptom")
    care_category: Mapped[str] = mapped_column(String(120))
    specialty_id: Mapped[str | None] = mapped_column(
        ForeignKey("specialties.id", ondelete="SET NULL"), index=True
    )
    specialty_code: Mapped[str] = mapped_column(String(64), index=True)
    secondary_specialty_codes: Mapped[list] = mapped_column(JSONB, default=list)
    urgency: Mapped[UrgencyLevel] = mapped_column(String(24), index=True)

    # Rule 10: every recommendation carries reason + confidence + next action.
    reason: Mapped[str] = mapped_column(Text)
    suggested_next_action: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)

    # Capability codes a facility must have for this patient's next step.
    required_capabilities: Mapped[list] = mapped_column(JSONB, default=list)
    recommended_tests: Mapped[list] = mapped_column(JSONB, default=list)
    patient_guidance: Mapped[str | None] = mapped_column(Text)
    knowledge_citations: Mapped[list] = mapped_column(JSONB, default=list)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    specialty: Mapped["Specialty"] = relationship()


class MedicalDocument(Base, TimestampMixin):
    """An uploaded report (PDF/JPG/PNG) before and after OCR."""

    __tablename__ = "medical_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    uploaded_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    file_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(80))
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    document_type: Mapped[DocumentType] = mapped_column(
        String(32), default=DocumentType.LAB_REPORT
    )

    processing_status: Mapped[ProcessingStatus] = mapped_column(
        String(24), default=ProcessingStatus.PENDING, index=True
    )
    processing_error: Mapped[str | None] = mapped_column(Text)
    report_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extracted: Mapped["ExtractedReport | None"] = relationship(
        back_populates="document", uselist=False, cascade="all, delete-orphan"
    )


class ExtractedReport(Base, TimestampMixin):
    """Structured OCR output: raw text, table rows and plain-language summary."""

    __tablename__ = "extracted_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("medical_documents.id", ondelete="CASCADE"), unique=True, index=True
    )

    ocr_engine: Mapped[str] = mapped_column(String(48), default="tesseract")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    ocr_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    page_count: Mapped[int] = mapped_column(Integer, default=1)

    provider_name: Mapped[str | None] = mapped_column(String(200))
    facility_name: Mapped[str | None] = mapped_column(String(200))
    patient_name_on_report: Mapped[str | None] = mapped_column(String(200))
    collection_date: Mapped[str | None] = mapped_column(String(64))
    report_title: Mapped[str | None] = mapped_column(String(200))

    # Preserved table structure, one entry per detected row.
    tables: Mapped[list] = mapped_column(JSONB, default=list)

    summary: Mapped[str | None] = mapped_column(Text)
    plain_language_explanation: Mapped[str | None] = mapped_column(Text)
    key_findings: Mapped[list] = mapped_column(JSONB, default=list)
    suggested_specialty_code: Mapped[str | None] = mapped_column(String(64))
    possible_next_step: Mapped[str | None] = mapped_column(Text)

    document: Mapped["MedicalDocument"] = relationship(back_populates="extracted")
    values: Mapped[list["ExtractedValue"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class ExtractedValue(Base, TimestampMixin):
    """One analyte row. The reference range printed on the report wins."""

    __tablename__ = "extracted_values"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("extracted_reports.id", ondelete="CASCADE"), index=True
    )

    test_name: Mapped[str] = mapped_column(String(200))
    normalised_name: Mapped[str | None] = mapped_column(String(120), index=True)
    result_text: Mapped[str] = mapped_column(String(120))
    result_numeric: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(64))

    # Verbatim from the report; only falls back to a catalogue range when the
    # report itself does not print one.
    reference_range_text: Mapped[str | None] = mapped_column(String(120))
    reference_low: Mapped[float | None] = mapped_column(Float)
    reference_high: Mapped[float | None] = mapped_column(Float)
    reference_source: Mapped[str] = mapped_column(String(24), default="report")

    flag: Mapped[ResultFlag] = mapped_column(String(16), default=ResultFlag.UNKNOWN)
    row_index: Mapped[int] = mapped_column(Integer, default=0)

    report: Mapped["ExtractedReport"] = relationship(back_populates="values")


class MedicalImage(Base, TimestampMixin):
    __tablename__ = "medical_images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    uploaded_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    file_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(80))
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    modality: Mapped[ImageModality] = mapped_column(
        String(32), default=ImageModality.CHEST_XRAY, index=True
    )

    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # Result of modality-appropriateness validation before inference runs.
    validation_passed: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_notes: Mapped[str | None] = mapped_column(Text)

    processing_status: Mapped[ProcessingStatus] = mapped_column(
        String(24), default=ProcessingStatus.PENDING, index=True
    )

    analysis: Mapped["ImageAnalysis | None"] = relationship(
        back_populates="image", uselist=False, cascade="all, delete-orphan"
    )


class ImageAnalysis(Base, TimestampMixin):
    """Screening-support output from a CV model adapter. Not a diagnosis."""

    __tablename__ = "image_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    image_id: Mapped[str] = mapped_column(
        ForeignKey("medical_images.id", ondelete="CASCADE"), unique=True, index=True
    )

    adapter_name: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(120))
    model_version: Mapped[str] = mapped_column(String(32), default="1.0.0")

    finding_label: Mapped[str] = mapped_column(String(120))
    finding_description: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    # Per-class probabilities, e.g. {"normal": 0.16, "pneumonia": 0.84}
    class_probabilities: Mapped[dict] = mapped_column(JSONB, default=dict)
    # What the model measured to reach that probability, when it can say —
    # each entry a code, a label, the measured value and its contribution to
    # the score. Persisted rather than recomputed because the image may be
    # deleted while the analysis is kept, and because a stored result must
    # keep showing the reasoning it was actually produced by.
    measurements: Mapped[list] = mapped_column(JSONB, default=list)
    # The boundary this result was judged against. Stored per row: a screening
    # model retuned for sensitivity later must not silently reinterpret
    # results decided under the old operating point.
    decision_threshold: Mapped[float | None] = mapped_column(Float)
    is_uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    uncertainty_note: Mapped[str | None] = mapped_column(Text)

    heatmap_path: Mapped[str | None] = mapped_column(String(512))
    has_visual_explanation: Mapped[bool] = mapped_column(Boolean, default=False)

    suggested_specialty_code: Mapped[str | None] = mapped_column(String(64))
    suggested_next_step: Mapped[str | None] = mapped_column(Text)
    inference_ms: Mapped[int] = mapped_column(Integer, default=0)

    image: Mapped["MedicalImage"] = relationship(back_populates="analysis")
