"""Where the encryption key comes from, and how it is allowed to change.

The industry pattern for encrypting medical records is *envelope encryption*:
records are encrypted with a data key (DEK), and the DEK is itself encrypted
by a key-encryption key (KEK) that lives in a hardware module or managed
service and never leaves it. Rotating means issuing a new DEK and leaving old
ciphertext readable under the old one — not decrypting and re-encrypting a
database.

The reason it is worth the trouble is regulatory rather than cryptographic.
Under the HIPAA Breach Notification Rule, protected health information
encrypted to NIST standards is *not* a reportable breach if the keys were not
also taken; GDPR Article 34(3)(a) says the same in different words. Sri
Lanka's Personal Data Protection Act No. 9 of 2022 imposes the controller
obligations that would apply here. Encryption at this layer is what turns a
stolen database from a notifiable catastrophe into an incident.

This module is the seam. `LocalKeyProvider` keeps the key in the environment,
which is right for a synthetic-data deployment and costs nothing.
`AwsKmsKeyProvider` documents the shape of the real thing and deliberately
raises rather than half-working, so nobody can select it and believe they have
key management they do not have.

The DEK is versioned by a short key id carried in the ciphertext, so a future
rotation is additive: new writes use the new key, old rows keep decrypting
under the one they were written with.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class KeyUnavailable(Exception):
    """No usable key for the requested operation."""


class KeyProvider(Protocol):
    """How the application obtains data keys."""

    def current(self) -> tuple[str, bytes]:
        """The (key_id, key) to encrypt new values with."""
        ...

    def by_id(self, key_id: str) -> bytes:
        """Resolve a historical key id so old ciphertext stays readable."""
        ...


class LocalKeyProvider:
    """Key from the environment. The dev and synthetic-data path.

    There is one key and its id is ``v1``, which keeps ciphertext written
    before key ids existed valid without touching a single row.
    """

    name = "local"
    CURRENT_KEY_ID = "v1"

    def current(self) -> tuple[str, bytes]:
        from app.core import crypto

        key = crypto._server_key()
        if key is None:
            raise KeyUnavailable("SUWAPATH_ENCRYPTION_KEY is not set.")
        return self.CURRENT_KEY_ID, key

    def by_id(self, key_id: str) -> bytes:
        from app.core import crypto

        if key_id != self.CURRENT_KEY_ID:
            raise KeyUnavailable(
                f"Ciphertext was written with key {key_id!r}, which this "
                "deployment does not hold."
            )
        key = crypto._server_key()
        if key is None:
            raise KeyUnavailable("SUWAPATH_ENCRYPTION_KEY is not set.")
        return key


class AwsKmsKeyProvider:
    """The production shape, deliberately not implemented.

    A real deployment would call ``kms:GenerateDataKey`` for a fresh DEK,
    store only the *wrapped* copy alongside its key id, and call ``kms:Decrypt``
    to unwrap it on demand. The plaintext DEK would be cached in memory for a
    bounded period and never written anywhere.

    It raises rather than falling back to the local key. A key provider that
    quietly degrades to reading an environment variable is worse than none,
    because the deployment believes its keys are in a hardware module.
    """

    name = "aws_kms"

    def __init__(self, key_arn: str) -> None:
        self.key_arn = key_arn

    def current(self) -> tuple[str, bytes]:
        raise NotImplementedError(
            "Wire boto3 kms:GenerateDataKey before selecting the KMS provider."
        )

    def by_id(self, key_id: str) -> bytes:
        raise NotImplementedError(
            "Wire boto3 kms:Decrypt before selecting the KMS provider."
        )


_provider: KeyProvider = LocalKeyProvider()


def get_provider() -> KeyProvider:
    return _provider


def set_provider(provider: KeyProvider) -> None:
    """Swap the provider. Intended for deployment wiring and tests."""
    global _provider
    _provider = provider
    logger.info("Key provider set to %s", getattr(provider, "name", provider))
