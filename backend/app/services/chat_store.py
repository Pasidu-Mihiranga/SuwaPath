"""Chat session lifecycle: history, resumption, and private mode.

Normal sessions are ordinary rows. Private sessions are the interesting case,
and the rule is simple enough to state in one line: **nothing a private
session says is ever written down.**

Concretely, for a private session this module writes a row containing the
session id, its owner, a PIN verifier, and an expiry — and nothing else. No
title, no message bodies, no metadata about what was discussed. The transcript
lives in a process-local buffer keyed by session id and disappears when the
session expires or the server restarts.

Why a PIN at all, if it is not encrypting anything? Because the threat here is
usually not a database attacker. It is a shared phone, a family member, a
partner. The PIN gates *resumption within the window*: someone who picks up an
unlocked phone cannot reopen the conversation, and the session does not appear
in the visible history list either way.

PIN handling
------------
Six digits is a million combinations, which brute-forces instantly if you let
it. Three defences: PBKDF2 with a per-session salt so a dump does not reveal
the PIN, a hard attempt cap after which the session self-destructs, and
constant-time comparison so timing does not leak digits.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.chat import ChatMessage, ChatSession

logger = logging.getLogger(__name__)

# A private session outlives the tab but not the day.
PRIVATE_TTL_HOURS = 12
# After this many wrong PINs the session is destroyed rather than locked, so
# there is nothing left to keep guessing at.
MAX_PIN_ATTEMPTS = 5
PIN_PATTERN = re.compile(r"^\d{6}$")

_PBKDF2_ROUNDS = 120_000

# How many turns of history to replay into the graph. Enough for a full
# history-taking loop without unbounded prompt growth.
HISTORY_TURNS = 12


# --------------------------------------------------------------------------
# Private transcripts — process memory only
# --------------------------------------------------------------------------
class _PrivateBuffer:
    """In-memory transcripts for private sessions. Never touches disk."""

    def __init__(self) -> None:
        self._data: dict[str, list[dict]] = {}
        self._touched: dict[str, float] = {}
        self._lock = threading.Lock()

    def append(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            self._evict()
            self._data.setdefault(session_id, []).append(
                {"role": role, "content": content}
            )
            self._touched[session_id] = time.monotonic()

    def read(self, session_id: str) -> list[dict]:
        with self._lock:
            return list(self._data.get(session_id, []))

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)
            self._touched.pop(session_id, None)

    def _evict(self) -> None:
        cutoff = time.monotonic() - PRIVATE_TTL_HOURS * 3600
        for key in [k for k, t in self._touched.items() if t < cutoff]:
            self._data.pop(key, None)
            self._touched.pop(key, None)


private_buffer = _PrivateBuffer()


# --------------------------------------------------------------------------
# PIN handling
# --------------------------------------------------------------------------
def hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", pin.encode(), salt.encode(), _PBKDF2_ROUNDS
    ).hex()


def verify_pin(session: ChatSession, pin: str) -> bool:
    if not session.pin_hash or not session.pin_salt:
        return False
    return hmac.compare_digest(session.pin_hash, hash_pin(pin, session.pin_salt))


class ChatError(Exception):
    """A chat operation the caller is not permitted to complete."""


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------
def create_session(
    db: Session,
    *,
    user_id: str,
    subject_user_id: str | None = None,
    language: str = "en",
    private: bool = False,
    pin: str | None = None,
) -> ChatSession:
    """Start a conversation. A private one requires a 6-digit PIN."""
    if private:
        if not pin or not PIN_PATTERN.match(pin):
            raise ChatError("A private chat needs a 6-digit PIN.")
        if len(set(pin)) == 1 or pin in {"123456", "654321", "012345"}:
            raise ChatError("Please choose a less predictable PIN.")

    salt = secrets.token_hex(16) if private else None
    session = ChatSession(
        user_id=user_id,
        subject_user_id=subject_user_id,
        language=language,
        is_private=private,
        # A private session gets no content-derived title, because the title
        # would leak the subject in exactly the list we promised to stay out of.
        title="Private conversation" if private else "New conversation",
        pin_hash=hash_pin(pin, salt) if private and pin and salt else None,
        pin_salt=salt,
        expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=PRIVATE_TTL_HOURS)
            if private
            else None
        ),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: Session, session_id: str, user_id: str) -> ChatSession | None:
    return db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id
        )
    ).scalar_one_or_none()


def resume_private(db: Session, session_id: str, user_id: str, pin: str) -> ChatSession:
    """Reopen a private session. Wrong PINs destroy it rather than lock it."""
    session = get_session(db, session_id, user_id)
    if session is None or not session.is_private:
        raise ChatError("That private chat is no longer available.")

    if session.expires_at and session.expires_at < datetime.now(timezone.utc):
        destroy(db, session)
        raise ChatError("That private chat has expired and is gone.")

    if verify_pin(session, pin):
        session.pin_attempts = 0
        db.commit()
        return session

    session.pin_attempts += 1
    remaining = MAX_PIN_ATTEMPTS - session.pin_attempts
    if remaining <= 0:
        destroy(db, session)
        raise ChatError(
            "Too many incorrect PINs — the conversation has been deleted."
        )
    db.commit()
    raise ChatError(f"Incorrect PIN. {remaining} attempt(s) left.")


def resume_private_by_pin(db: Session, user_id: str, pin: str) -> ChatSession:
    """Reopen a private session knowing only its PIN.

    Requiring a session id as well was a UI failure dressed up as security. The
    id is not a secret — it identifies the row, and it is already scoped to the
    caller's own account, so asking the user to keep a UUID safe bought nothing
    and made the feature unusable in practice.

    The search is over the caller's own live private sessions only, so the PIN
    is never tested against anyone else's conversation. In the ordinary case
    there is exactly one.

    A wrong PIN counts against every candidate, because the server genuinely
    cannot tell which one was meant. With a single private chat — the normal
    situation — that is identical to the old behaviour.
    """
    now = datetime.now(timezone.utc)
    candidates = list(
        db.execute(
            select(ChatSession).where(
                ChatSession.user_id == user_id,
                ChatSession.is_private.is_(True),
            )
        ).scalars()
    )

    live: list[ChatSession] = []
    for session in candidates:
        if session.expires_at and session.expires_at < now:
            destroy(db, session)
        else:
            live.append(session)

    if not live:
        raise ChatError("There is no private chat to reopen.")

    for session in live:
        if verify_pin(session, pin):
            session.pin_attempts = 0
            db.commit()
            return session

    lowest_remaining = MAX_PIN_ATTEMPTS
    for session in live:
        session.pin_attempts += 1
        remaining = MAX_PIN_ATTEMPTS - session.pin_attempts
        lowest_remaining = min(lowest_remaining, remaining)
        if remaining <= 0:
            destroy(db, session)
    db.commit()

    if lowest_remaining <= 0:
        raise ChatError("Too many incorrect PINs — the conversation has been deleted.")
    raise ChatError(f"Incorrect PIN. {lowest_remaining} attempt(s) left.")


def destroy(db: Session, session: ChatSession) -> None:
    """Remove a session and everything associated with it."""
    private_buffer.drop(session.id)
    db.delete(session)
    db.commit()


def list_sessions(db: Session, user_id: str, *, limit: int = 40) -> list[ChatSession]:
    """Visible history. Private sessions are excluded by design."""
    return list(
        db.execute(
            select(ChatSession)
            .where(
                ChatSession.user_id == user_id,
                ChatSession.is_private.is_(False),
                ChatSession.is_archived.is_(False),
            )
            .order_by(ChatSession.last_message_at.desc().nullslast())
            .limit(limit)
        ).scalars()
    )


def load_history(db: Session, session: ChatSession) -> list[dict]:
    """Prior turns for the graph, newest-last, capped."""
    if session.is_private:
        return private_buffer.read(session.id)[-HISTORY_TURNS:]

    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(HISTORY_TURNS)
    ).scalars()
    return [{"role": r.role, "content": r.content} for r in reversed(list(rows))]


def append(
    db: Session,
    session: ChatSession,
    *,
    role: str,
    content: str,
    meta: dict | None = None,
) -> None:
    """Record a turn.

    For a private session this writes to process memory and updates only the
    counters on the row — the content itself is never persisted.
    """
    if session.is_private:
        private_buffer.append(session.id, role, content)
    else:
        db.add(
            ChatMessage(
                session_id=session.id,
                role=role,
                content=content,
                meta=meta or {},
            )
        )
        # Title the conversation from the patient's own opening words. Using
        # their text rather than a generated summary means the history list
        # cannot say something the patient never did.
        if role == "user" and session.message_count == 0:
            session.title = _title_from(content)
        # A crisis disclosure must not become a heading someone else can read
        # over their shoulder. The conversation is still theirs and still
        # deletable — only the label is made neutral.
        if role == "assistant" and (meta or {}).get("guard", {}).get("input") == "crisis":
            session.title = "Support conversation"

    session.message_count += 1
    session.last_message_at = datetime.now(timezone.utc)
    db.commit()


def _title_from(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= 60:
        return cleaned or "New conversation"
    return cleaned[:60].rsplit(" ", 1)[0] + "…"


def purge_expired(db: Session) -> int:
    """Delete private sessions past their expiry. Safe to call repeatedly."""
    expired = list(
        db.execute(
            select(ChatSession).where(
                ChatSession.is_private.is_(True),
                ChatSession.expires_at < datetime.now(timezone.utc),
            )
        ).scalars()
    )
    for session in expired:
        private_buffer.drop(session.id)
        db.delete(session)
    if expired:
        db.commit()
    return len(expired)
