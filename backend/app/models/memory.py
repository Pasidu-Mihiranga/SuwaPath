"""Durable facts the assistant has learned about a patient.

Session memory (``app/agent/cag.py``) evaporates on restart, which is right
for "they said 7 out of 10 two turns ago" and wrong for "they are allergic to
penicillin". This table holds the second kind.

**This is not a medical record.** Nothing here is authoritative, nothing here
is shown to a clinician as fact, and nothing here can change urgency. It
exists so the assistant does not ask the same question every week. The
structured record in `patient_profiles` and the clinical tables remains the
source of truth; if the two disagree, the record wins.

Three properties make that safe:

- **Provenance is stored.** Every fact records the conversation turn it came
  from, so a patient can be shown why the assistant believes something.
- **Facts expire.** A `confidence` that decays and an optional `expires_at`
  mean a stale belief ("currently pregnant") stops being asserted rather than
  persisting forever.
- **Patients can delete them.** A memory the patient disowns is removed, not
  flagged.

Private conversations never write here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class MemoryKind:
    """What sort of thing a fact is. Plain strings — see the enum note in LOG.md."""

    PROFILE = "profile"        # stable: occupation, lives alone, no transport
    CLINICAL = "clinical"      # allergy, chronic condition, past procedure
    PREFERENCE = "preference"  # prefers teleconsultation, prefers Tamil
    CONTEXT = "context"        # currently pregnant, recently bereaved
    EPISODIC = "episodic"      # "asked about a headache on 3 Aug"


class PatientMemory(Base, TimestampMixin):
    """One durable fact, scoped to one patient."""

    __tablename__ = "patient_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    patient_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[str] = mapped_column(String(24), default=MemoryKind.CONTEXT, index=True)
    # A short stable key ("allergy", "occupation") so a newer value replaces
    # the older one instead of accumulating contradictions.
    key: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(Text)

    # 0-1. Decays with age for CONTEXT facts; stays put for CLINICAL ones.
    confidence: Mapped[float] = mapped_column(Float, default=0.6)

    # Provenance — which conversation, and the patient's own words.
    source_session_id: Mapped[str | None] = mapped_column(String(36))
    source_quote: Mapped[str | None] = mapped_column(Text)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        # One current value per key per patient. Re-learning updates in place.
        UniqueConstraint("patient_user_id", "key", name="uq_patient_memory_key"),
        Index("ix_patient_memories_lookup", "patient_user_id", "kind"),
    )
