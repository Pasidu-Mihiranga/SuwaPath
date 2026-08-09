"""Tables for work the system does without being asked.

Everything else in this application is a reaction: a request arrives, rows are
read or written, a response goes back. These four tables exist so the system
can notice something, decide what to do about it, and carry that decision
across process restarts and across the hours between proposing an action and a
human approving it.

- `AgentTask`  — a unit of autonomous work, deduplicated by intent.
- `TaskRun`    — one attempt at a task, kept so failures are inspectable.
- `ActionProposal` — an action prepared for a human to approve.
- `DeliveryAttempt` — whether a message actually reached anyone.

**Why the dedupe key is the important column.** Detectors run repeatedly — every
15 minutes, every day — over data that changes slowly. Without deduplication a
patient with one unconverted referral would be nudged every single run.
Handler-level idempotency is the usual answer and it is weaker than this one:
`dedupe_key` is UNIQUE and carries the *identity of the intent*
(`referral_unconverted:{id}:{stage}`), so a repeat enqueue is rejected by the
database and the handler never runs at all.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
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


class AgentTask(Base, TimestampMixin):
    """One piece of work the system decided to do on its own."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_agent_tasks_dedupe_key"),
        Index("ix_agent_tasks_claimable", "status", "run_after"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    kind: Mapped[str] = mapped_column(String(64), index=True)
    # The identity of the intent, not of the run. See the module docstring.
    dedupe_key: Mapped[str] = mapped_column(String(255))

    # Who this is about. Nullable because operational tasks (a no-show sweep,
    # a digest) belong to no single patient.
    subject_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_error: Mapped[str | None] = mapped_column(Text)

    # Lease fields: a worker that dies mid-task leaves these set, and the
    # recovery sweep uses them to tell "running" from "abandoned".
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    runs: Mapped[list["TaskRun"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskRun(Base, TimestampMixin):
    """One attempt at a task.

    Kept separately from the task so a task that succeeded on its third try
    still shows the two failures. "It worked eventually" and "it worked" are
    different facts, and only one of them needs investigating.
    """

    __tablename__ = "agent_task_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), index=True
    )

    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # ok | error
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)

    task: Mapped[AgentTask] = relationship(back_populates="runs")


class ActionProposal(Base, TimestampMixin):
    """An action the system prepared and a human has not yet approved.

    This is the alternative to a graph interrupt, chosen because most proposals
    originate in a background job with no graph thread at all, and because the
    human may not answer for hours — long after any request has ended. A row
    survives both.

    `args` is a record of what was proposed, **not** an authorisation. The
    approve endpoint re-derives permission and re-validates freshness before
    executing; trusting stored args would let a proposal made under one consent
    state execute under another.
    """

    __tablename__ = "action_proposals"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_action_proposals_idem"),
        Index("ix_action_proposals_inbox", "subject_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # Whose care this concerns — the patient, even when a guardian approves it.
    subject_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    origin: Mapped[str] = mapped_column(String(16), default="job")  # job | chat
    origin_ref: Mapped[str | None] = mapped_column(String(64))

    action_name: Mapped[str] = mapped_column(String(64), index=True)
    args: Mapped[dict] = mapped_column(JSONB, default=dict)
    risk_tier: Mapped[str] = mapped_column(String(4), default="T1")

    # Shown to the human. Written by the proposer so the approval screen never
    # has to reconstruct clinical reasoning from raw arguments.
    title: Mapped[str] = mapped_column(String(160))
    preview_text: Mapped[str] = mapped_column(Text)
    # Why the system believes this: a detector id, a rule id, a referral id.
    # T0 alerts refuse to execute without it, so a model cannot invent grounds.
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)

    idempotency_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSONB, default=dict)


class DeliveryAttempt(Base, TimestampMixin):
    """Whether a message actually reached someone.

    A notification row is not a delivery. Separating the two is what makes
    "escalate to SMS if still unread after an hour" expressible, and what makes
    delivery rate measurable rather than assumed.
    """

    __tablename__ = "delivery_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    notification_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("notifications.id", ondelete="CASCADE"), index=True
    )
    recipient_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    channel: Mapped[str] = mapped_column(String(16))  # in_app | sms
    status: Mapped[str] = mapped_column(String(24))
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
