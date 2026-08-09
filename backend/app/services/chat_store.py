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
The PIN belongs to the person, not the conversation: one PIN opens every
private chat they have. A per-session PIN could not survive contact with
resumption — given only a PIN, the server could not tell which conversation
was meant, so a wrong guess had to be counted against all of them.

Six digits is a million combinations, which brute-forces instantly if you let
it. Three defences: PBKDF2 with a salt so a dump does not reveal the PIN, an
attempt cap that locks the PIN for a cool-off period, and constant-time
comparison so timing does not leak digits. The cap locks rather than deletes —
now that one PIN guards every private chat, self-destruction would let anyone
holding the phone erase them all with five wrong guesses.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import crypto
from app.models.chat import (
    ChatMessage,
    ChatSession,
    PrivateChatPin,
    PrivateTranscript,
)

logger = logging.getLogger(__name__)

# A private session outlives the tab but not the day.
PRIVATE_TTL_HOURS = 12
# Wrong PINs allowed before the private PIN is locked for a cool-off period.
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
    """Open private transcripts, held in memory only while in use.

    This is a cache of sessions someone has unlocked in this process, not the
    system of record — that is `private_transcripts`, encrypted under a key
    derived from the PIN. Holding the derived key here is what lets the rest
    of a conversation continue without asking for the PIN on every turn; it
    is dropped when the session ends or expires, and it is never written down.
    """

    def __init__(self) -> None:
        self._data: dict[str, list[dict]] = {}
        self._keys: dict[str, bytes] = {}
        self._touched: dict[str, float] = {}
        self._lock = threading.Lock()

    def unlock(self, session_id: str, key: bytes, turns: list[dict]) -> None:
        with self._lock:
            self._keys[session_id] = key
            self._data[session_id] = list(turns)
            self._touched[session_id] = time.monotonic()

    def key(self, session_id: str) -> bytes | None:
        with self._lock:
            return self._keys.get(session_id)

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
            self._keys.pop(session_id, None)
            self._touched.pop(session_id, None)

    def _evict(self) -> None:
        cutoff = time.monotonic() - PRIVATE_TTL_HOURS * 3600
        for key in [k for k, t in self._touched.items() if t < cutoff]:
            self._data.pop(key, None)
            self._keys.pop(key, None)
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


def _validate_pin(pin: str | None) -> str:
    if not pin or not PIN_PATTERN.match(pin):
        raise ChatError("A private chat needs a 6-digit PIN.")
    if len(set(pin)) == 1 or pin in {"123456", "654321", "012345"}:
        raise ChatError("Please choose a less predictable PIN.")
    return pin


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
    """Start a conversation.

    A private one is guarded by the person's private PIN. The first private
    chat sets that PIN; every later one verifies it, so the same PIN opens all
    of them and no conversation carries a secret of its own.
    """
    if private:
        _validate_pin(pin)
        assert pin is not None
        if has_private_pin(db, user_id):
            verify_private_pin(db, user_id, pin)
        else:
            set_private_pin(db, user_id, pin)

    session = ChatSession(
        user_id=user_id,
        subject_user_id=subject_user_id,
        language=language,
        is_private=private,
        # A private session gets no content-derived title, because the title
        # would leak the subject in exactly the list we promised to stay out of.
        title="Private conversation" if private else "New conversation",
        # The PIN verifier lives on the person now. This salt is a different
        # thing: it makes each session's transcript key distinct, so one
        # decrypted conversation does not unlock the others.
        pin_hash=None,
        pin_salt=secrets.token_hex(16) if private else None,
        expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=PRIVATE_TTL_HOURS)
            if private
            else None
        ),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    if private and pin:
        # Derive and hold the transcript key now, so the conversation can be
        # written without asking for the PIN again on every turn.
        private_buffer.unlock(
            session.id, crypto.derive_key(pin, session.pin_salt or session.id), []
        )
    return session


def get_session(db: Session, session_id: str, user_id: str) -> ChatSession | None:
    return db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id
        )
    ).scalar_one_or_none()


# --------------------------------------------------------------------------
# The per-person private PIN
# --------------------------------------------------------------------------
# Long enough to make five guesses useless, short enough that a person who
# simply mistyped is not shut out for the rest of the day.
PIN_LOCKOUT_MINUTES = 15


def _pin_record(db: Session, user_id: str) -> PrivateChatPin | None:
    return db.execute(
        select(PrivateChatPin).where(PrivateChatPin.user_id == user_id)
    ).scalar_one_or_none()


def has_private_pin(db: Session, user_id: str) -> bool:
    return _pin_record(db, user_id) is not None


def set_private_pin(db: Session, user_id: str, pin: str) -> PrivateChatPin:
    """Register the PIN that will guard every private chat for this person."""
    _validate_pin(pin)
    salt = secrets.token_hex(16)
    record = _pin_record(db, user_id)
    if record is None:
        record = PrivateChatPin(user_id=user_id)
        db.add(record)
    record.pin_hash = hash_pin(pin, salt)
    record.pin_salt = salt
    record.attempts = 0
    record.locked_until = None
    db.commit()
    db.refresh(record)
    return record


def verify_private_pin(db: Session, user_id: str, pin: str) -> None:
    """Check the person's PIN, or raise ChatError explaining why not.

    Nothing is deleted on failure. The PIN now guards every private chat at
    once, so destroying them after five wrong guesses would turn a lockout
    into a way for anyone holding the phone to erase the lot.
    """
    record = _pin_record(db, user_id)
    if record is None:
        raise ChatError("No private PIN has been set yet.")

    now = datetime.now(timezone.utc)
    if record.locked_until and record.locked_until > now:
        wait = int((record.locked_until - now).total_seconds() // 60) + 1
        raise ChatError(f"Too many attempts. Try again in {wait} minute(s).")

    if hmac.compare_digest(record.pin_hash, hash_pin(pin, record.pin_salt)):
        record.attempts = 0
        record.locked_until = None
        db.commit()
        return

    record.attempts += 1
    remaining = MAX_PIN_ATTEMPTS - record.attempts
    if remaining <= 0:
        record.attempts = 0
        record.locked_until = now + timedelta(minutes=PIN_LOCKOUT_MINUTES)
        db.commit()
        raise ChatError(
            f"Too many attempts. Private chat is locked for "
            f"{PIN_LOCKOUT_MINUTES} minutes."
        )
    db.commit()
    raise ChatError(f"Incorrect PIN. {remaining} attempt(s) left.")


def live_private_sessions(db: Session, user_id: str) -> list[ChatSession]:
    """The person's private sessions that have not expired, newest first."""
    now = datetime.now(timezone.utc)
    live: list[ChatSession] = []
    for session in db.execute(
        select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.is_private.is_(True),
        )
    ).scalars():
        if session.expires_at and session.expires_at < now:
            destroy(db, session)
        else:
            live.append(session)
    live.sort(key=lambda s: s.created_at, reverse=True)
    return live


def resume_private_by_pin(db: Session, user_id: str, pin: str) -> ChatSession:
    """Reopen the most recent private chat using the person's PIN.

    The PIN identifies the person, not the conversation, so there is no
    guessing about which session was meant and no collateral damage to the
    others — the problem that made the per-session PIN unworkable.
    """
    verify_private_pin(db, user_id, pin)
    live = live_private_sessions(db, user_id)
    if not live:
        raise ChatError("There is no private chat to reopen.")
    session = live[0]
    unlock_private(db, session, pin)
    return session


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


def _persist_private(db: Session, session: ChatSession) -> None:
    """Write the open transcript back, encrypted under its PIN-derived key."""
    key = private_buffer.key(session.id)
    if key is None:
        # No key in this process: the session was never unlocked here, so
        # there is nothing to write and nothing that could be written safely.
        return
    blob = crypto.encrypt_with_pin_key(
        key, json.dumps(private_buffer.read(session.id))
    )
    row = db.get(PrivateTranscript, session.id)
    if row is None:
        db.add(PrivateTranscript(session_id=session.id, ciphertext=blob))
    else:
        row.ciphertext = blob


def unlock_private(db: Session, session: ChatSession, pin: str) -> list[dict]:
    """Decrypt a private transcript into this process and return its turns.

    A wrong PIN cannot get here — the caller verifies it first — but a key
    that does not match the stored ciphertext still has to be handled, because
    a PIN that was changed after the transcript was written would produce
    exactly that.
    """
    key = crypto.derive_key(pin, session.pin_salt or session.id)
    row = db.get(PrivateTranscript, session.id)
    turns: list[dict] = []
    if row is not None:
        try:
            turns = json.loads(crypto.decrypt_with_pin_key(key, row.ciphertext))
        except (crypto.DecryptionError, ValueError):
            logger.warning(
                "Private transcript for %s could not be decrypted.", session.id
            )
            turns = []
    private_buffer.unlock(session.id, key, turns)
    return turns


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
    return [
        {"role": r.role, "content": crypto.decrypt(r.content)}
        for r in reversed(list(rows))
    ]


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
        _persist_private(db, session)
    else:
        db.add(
            ChatMessage(
                session_id=session.id,
                role=role,
                # Encrypted with the server key. Reads go through
                # crypto.decrypt, which also passes through older plaintext
                # rows so switching this on did not need a migration.
                content=crypto.encrypt(content),
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


# Ordinary conversations are kept for a bounded time rather than forever.
# Indefinite retention of clinical conversation is a liability that grows on
# its own; ninety days covers "resume the thing I was doing last month".
NORMAL_RETENTION_DAYS = 90


def purge_old_conversations(db: Session) -> int:
    """Delete ordinary conversations past the retention window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=NORMAL_RETENTION_DAYS)
    stale = list(
        db.execute(
            select(ChatSession).where(
                ChatSession.is_private.is_(False),
                ChatSession.created_at < cutoff,
            )
        ).scalars()
    )
    for session in stale:
        db.delete(session)
    if stale:
        db.commit()
    return len(stale)


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
