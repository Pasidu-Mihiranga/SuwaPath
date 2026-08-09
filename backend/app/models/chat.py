"""Assistant conversations.

Two kinds of session live here, and the difference is the whole point:

**Normal sessions** are persisted so the patient can scroll back, resume a
consultation the next day, and have the assistant remember what it was already
told. Continuity is a clinical benefit — a patient who has to re-explain their
history from scratch gives a worse history each time.

**Private sessions** are the opposite promise. Someone asking about an STI, a
pregnancy they have not told anyone about, or their mental health needs to
know the conversation is not sitting in a list their family could open on a
shared phone. So a private session stores:

- no message bodies, ever — not encrypted, not hashed, simply not written
- no title derived from content
- only a row proving the session existed, its PIN verifier, and when it expires

The messages live in process memory for the life of the session and are gone
when it expires. Resuming needs the PIN, and the PIN is stored as a salted
hash, so a database dump does not yield it.

This is a deliberate trade: a private conversation cannot be recovered if the
PIN is lost. Being unrecoverable is the feature.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid


class ChatSession(Base, TimestampMixin):
    """One conversation with the assistant."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # A guardian may hold a conversation about a dependent.
    subject_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )

    # Derived from the first message, never model-written, and always empty
    # for private sessions.
    title: Mapped[str] = mapped_column(String(160), default="New conversation")
    language: Mapped[str] = mapped_column(String(8), default="en")

    is_private: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # PBKDF2 of the 6-digit PIN. Null for normal sessions.
    pin_hash: Mapped[str | None] = mapped_column(String(255))
    pin_salt: Mapped[str | None] = mapped_column(String(64))
    # Throttles PIN guessing: six digits is only a million combinations.
    pin_attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )

    __table_args__ = (
        Index("ix_chat_sessions_user_recent", "user_id", "last_message_at"),
    )


class ChatMessage(Base, TimestampMixin):
    """One turn. Never written for a private session."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)

    # How the answer was produced, kept so the UI can show provenance and so
    # quality can be audited later: routes taken, provider, cache hit, latency.
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class PrivateChatPin(Base, TimestampMixin):
    """One private-chat PIN per person, rather than one per conversation.

    A per-session PIN forced an impossible choice on resume: with only a PIN to
    go on, the server could not tell which conversation was meant, so a wrong
    guess had to count against every private session the user had. One PIN per
    person removes the ambiguity entirely — the PIN identifies the person, and
    the person's private sessions are then all reachable.

    Failed attempts lock the PIN for a while instead of destroying anything.
    Destruction made sense when a wrong PIN could only mean an intruder on one
    conversation; now that a single PIN guards every private chat, deleting
    them all on five wrong guesses would hand any passer-by a way to wipe the
    lot. A lockout costs an attacker time and costs the owner nothing
    permanent.

    This table is deliberately separate from `users`: it holds a credential,
    not a profile attribute, and keeping it apart means a query for user data
    does not casually carry a PIN verifier along with it.
    """

    __tablename__ = "private_chat_pins"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pin_hash: Mapped[str] = mapped_column(String(255))
    pin_salt: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
