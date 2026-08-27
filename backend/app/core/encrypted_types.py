"""Column types that encrypt on the way in and decrypt on the way out.

A model column becomes encrypted by changing its type annotation and nothing
else. Queries, relationships and serialisation are unaffected, because the
application only ever sees plaintext.

What must never be encrypted with these
---------------------------------------
Anything a SQL query filters, sorts or joins on. AES-GCM is randomised — the
same value encrypts differently every time — so `WHERE column = 'x'` matches
nothing and, worse, matches nothing *silently*: an empty result set is not an
error. Before encrypting a column, grep for it in query builders.

Two live examples in this codebase:

* ``User.full_name`` and ``User.email`` are searched with ``LIKE`` in the admin
  console. Encrypting them would break patient lookup with no error message.
  They are deliberately excluded.
* ``PatientProfile.latitude`` / ``.longitude`` and ``.chronic_conditions``
  looked like the same risk and are not: every read path fetches one profile
  row by ``user_id`` and does the geometry and rule checks in Python. Verified
  by grep before encrypting them, and worth re-verifying if anyone adds a
  cohort search.

Where a value genuinely must be both encrypted and searchable, the pattern is
a blind index — store an HMAC of the normalised value in a second column and
query that. Nothing here needs one yet, and adding one speculatively would be
a second thing to keep in sync.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.types import Text, TypeDecorator

from app.core import crypto
from app.core.config import settings

logger = logging.getLogger(__name__)


def _should_encrypt() -> bool:
    """Whether to encrypt new writes.

    Without a key, values are written as plaintext and read back unchanged —
    the same behaviour the chat store has always had, so a demo without a key
    still runs. The startup guard in `app/main.py` is what makes this
    impossible in production; here it would only turn a missing environment
    variable into a stack trace on every insert.
    """
    return crypto.is_enabled()


class EncryptedString(TypeDecorator):
    """Text, encrypted at rest.

    Reads pass plaintext through untouched, so rows written before a column
    was encrypted stay readable and no backfill is required to deploy this.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        if not _should_encrypt():
            return value
        return crypto.encrypt_enveloped(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if not crypto.looks_encrypted(value):
            return value  # written before the column was encrypted
        try:
            return crypto.decrypt_enveloped(value)
        except Exception:  # noqa: BLE001 — one unreadable row must not 500
            logger.exception("Could not decrypt column value.")
            return None


class EncryptedJSON(TypeDecorator):
    """A JSON-serialisable value, encrypted at rest.

    Backed by ``Text`` rather than ``JSONB``: ciphertext is not valid JSON, so
    the underlying column type has to change. That is a real schema change and
    the reason this needs a re-seed rather than a live migration.

    Postgres JSON operators (``@>``, ``->>``) stop working on these columns,
    which is fine only because nothing queries them that way — the same
    constraint the module docstring describes, and it holds here for the same
    reason.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        raw = json.dumps(value, default=str)
        if not _should_encrypt():
            return raw
        return crypto.encrypt_enveloped(raw)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        raw = value
        if crypto.looks_encrypted(raw):
            try:
                raw = crypto.decrypt_enveloped(raw)
            except Exception:  # noqa: BLE001
                # Returning the placeholder string `crypto.decrypt` uses would
                # be worse than returning nothing here: a clinician reading
                # "[encrypted — could not be read]" in an allergies list has
                # been shown something that looks like clinical content and is
                # not. An empty list is unmistakably absent.
                logger.exception("Could not decrypt JSON column; returning empty.")
                return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            # Legacy plaintext that was never valid JSON. Hand it back rather
            # than discarding data that predates this type.
            return raw
