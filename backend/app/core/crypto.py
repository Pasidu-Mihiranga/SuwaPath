"""Application-level encryption for conversation content.

Why here and not just at the disk or database layer
---------------------------------------------------
Disk encryption protects a stolen server. It does nothing about the cases that
actually happen: a leaked backup file, a database snapshot copied to a laptop,
a read-only replica handed to an analyst, or an SQL injection that returns
rows. In all of those the database hands over plaintext quite happily, because
from its point of view the reader is authorised.

Encrypting the column means the ciphertext is what leaks.

The scheme
----------
AES-256-GCM, which authenticates as well as encrypts: a modified ciphertext
fails to decrypt rather than yielding altered text. That matters for medical
content, where a silently corrupted "no known allergies" is worse than an
error.

Two kinds of key are used, for two different threats:

**Server key** (`SUWAPATH_ENCRYPTION_KEY`) protects ordinary conversations.
The server must be able to read these — that is the point of history — so the
key lives with the application. This defends against the database being read
by someone who is not the application.

**PIN-derived key** protects private conversations. Derived with PBKDF2 from
the PIN the user types, never stored anywhere. The server can only read a
private transcript while someone who knows the PIN is using it. A database
dump plus the entire application configuration is still not enough.

Stored format
-------------
`v1.<base64 nonce>.<base64 ciphertext>` — the version prefix is what makes key
rotation possible later without guessing how any given row was written.

Key handling
------------
The key comes from the environment, never from the codebase. If it is absent
the application still runs and stores plaintext, because a missing key must
not take down a clinic; it logs a warning at startup instead. `is_enabled()`
lets deployment checks assert the real answer in production.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core import key_provider
from app.core.config import settings

logger = logging.getLogger(__name__)

_ENV_VAR = "SUWAPATH_ENCRYPTION_KEY"
# v1 is the original three-field token and is still what every stored row
# uses. v2 adds the key id so a rotation can leave old ciphertext readable.
# New writes through the envelope path use v2; `encrypt()` still emits v1 so
# nothing changes for callers that have not opted in.
_VERSION_V1 = "v1"
_VERSION_V2 = "v2"
_VERSION = _VERSION_V1  # what _encrypt_with emits
_NONCE_BYTES = 12  # 96 bits, the size AES-GCM is specified for
_PBKDF2_ROUNDS = 210_000  # OWASP's 2023 floor for PBKDF2-HMAC-SHA256


class DecryptionError(Exception):
    """Ciphertext could not be read with the key supplied."""


def _server_key() -> bytes | None:
    # Read through Settings, not os.getenv: the project keeps configuration in
    # .env loaded by pydantic-settings, which never exports to the process
    # environment. Reading the environment directly silently found nothing and
    # stored plaintext while appearing to work.
    raw = (settings.suwapath_encryption_key or os.getenv(_ENV_VAR, "")).strip()
    if not raw:
        return None
    try:
        key = base64.urlsafe_b64decode(raw)
    except Exception:
        key = raw.encode()
    if len(key) != 32:
        # Accept any passphrase but stretch it to a real key length rather
        # than failing — a short key is a deployment mistake, not a reason to
        # refuse to start.
        key = hashlib.sha256(key).digest()
    return key


def is_enabled() -> bool:
    """True when a server key is configured. Deployment checks assert this."""
    return _server_key() is not None


def derive_key(secret: str, salt: str) -> bytes:
    """Stretch a PIN into an AES key. Used for private transcripts."""
    return hashlib.pbkdf2_hmac(
        "sha256", secret.encode(), salt.encode(), _PBKDF2_ROUNDS, dklen=32
    )


def _encrypt_with(key: bytes, plaintext: str) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    blob = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return ".".join(
        (
            _VERSION,
            base64.urlsafe_b64encode(nonce).decode(),
            base64.urlsafe_b64encode(blob).decode(),
        )
    )


def _split_token(token: str) -> tuple[str, str, str, str]:
    """Return (version, key_id, nonce_b64, blob_b64) for either format.

    ``v1.<nonce>.<blob>`` predates key ids and is what every currently stored
    row uses. ``v2.<key_id>.<nonce>.<blob>`` carries the id of the key it was
    written with, so rotation can add a key without rewriting anything.

    Both must keep parsing. A version-aware rewrite that assumed three fields
    would fail to read every chat message and private transcript already in
    the database.
    """
    parts = token.split(".")
    if len(parts) == 3 and parts[0] == _VERSION_V1:
        return _VERSION_V1, key_provider.LocalKeyProvider.CURRENT_KEY_ID, parts[1], parts[2]
    if len(parts) == 4 and parts[0] == _VERSION_V2:
        return _VERSION_V2, parts[1], parts[2], parts[3]
    raise DecryptionError("Not an encrypted value.")


def _decrypt_with(key: bytes, token: str) -> str:
    _version, _key_id, nonce_b64, blob_b64 = _split_token(token)
    try:
        plaintext = AESGCM(key).decrypt(
            base64.urlsafe_b64decode(nonce_b64),
            base64.urlsafe_b64decode(blob_b64),
            None,
        )
    except (InvalidTag, ValueError) as exc:
        raise DecryptionError("Wrong key, or the value was tampered with.") from exc
    return plaintext.decode()


def looks_encrypted(value: str) -> bool:
    return value.startswith((f"{_VERSION_V1}.", f"{_VERSION_V2}."))


# --------------------------------------------------------------------------
# Envelope path — key id carried in the ciphertext
# --------------------------------------------------------------------------
def encrypt_enveloped(plaintext: str) -> str:
    """Encrypt with the current data key, recording which key was used.

    Used by the encrypted column types. Unlike `encrypt()` this raises when no
    key is configured rather than returning plaintext: a column declared
    encrypted that silently stores readable text is the failure mode the whole
    exercise exists to prevent. `EncryptedString` decides whether a keyless
    deployment is acceptable, and it is the only place that decision is made.
    """
    key_id, key = key_provider.get_provider().current()
    nonce = os.urandom(_NONCE_BYTES)
    blob = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return ".".join(
        (
            _VERSION_V2,
            key_id,
            base64.urlsafe_b64encode(nonce).decode(),
            base64.urlsafe_b64encode(blob).decode(),
        )
    )


def decrypt_enveloped(token: str) -> str:
    """Read a value written by `encrypt_enveloped`, or any v1 ciphertext."""
    _version, key_id, _nonce, _blob = _split_token(token)
    key = key_provider.get_provider().by_id(key_id)
    return _decrypt_with(key, token)


# --------------------------------------------------------------------------
# Ordinary conversations — server key
# --------------------------------------------------------------------------
def encrypt(plaintext: str) -> str:
    """Encrypt with the server key, or return the text unchanged if unset."""
    key = _server_key()
    if key is None:
        return plaintext
    return _encrypt_with(key, plaintext)


def decrypt(value: str) -> str:
    """Read a stored value, whether or not it was encrypted.

    Rows written before encryption was switched on are plaintext and stay
    readable — the alternative is a migration that must run perfectly before
    anyone can open their history. Ciphertext that will not decrypt returns a
    placeholder rather than raising: one unreadable message should not take
    down the whole conversation.
    """
    if not value or not looks_encrypted(value):
        return value
    key = _server_key()
    if key is None:
        logger.error("Encrypted content found but %s is not set.", _ENV_VAR)
        return "[encrypted — server key unavailable]"
    try:
        return _decrypt_with(key, value)
    except DecryptionError:
        logger.exception("Could not decrypt stored content.")
        return "[encrypted — could not be read]"


# --------------------------------------------------------------------------
# Private conversations — PIN-derived key
# --------------------------------------------------------------------------
def encrypt_with_pin_key(key: bytes, plaintext: str) -> str:
    return _encrypt_with(key, plaintext)


def decrypt_with_pin_key(key: bytes, token: str) -> str:
    """Raises DecryptionError on a wrong PIN — the caller must handle it."""
    return _decrypt_with(key, token)
