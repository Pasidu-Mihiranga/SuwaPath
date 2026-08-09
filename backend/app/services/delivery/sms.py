"""SMS, and the rule that makes it safe to use for health.

**An SMS from SuwaPath never contains clinical content.** Not the condition,
not the specialty, not the test, not the medication. The message says there is
something to look at and where to look.

That is not caution for its own sake. SMS is unencrypted in transit, is
retained by the aggregator, and lands on a lock screen that anybody nearby can
read — often a shared handset. This product already has a private mode built
around the fact that the realistic threat is a family member rather than an
attacker; sending "your STI results are ready" by SMS would undo that in one
line. The lock-screen preview *is* the threat model.

The single exception is a life-threatening escalation, which may carry the
deterministic instruction from the red-flag engine — "call 1990 now" — because
it names no condition and the cost of it not arriving is unbounded.

No provider is configured by default. An unconfigured channel records
`skipped_no_provider` and the router falls back to in-app, which mirrors how
the LLM layer degrades: the feature announces its absence rather than failing.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.identity import User
from app.privacy.boundary import _EGRESS_PATTERNS
from app.services.delivery.base import DeliveryResult, Message

logger = logging.getLogger(__name__)

GENERIC_BODY = "SuwaPath: you have an important update. Open the app to view it."

# Words that must never leave in a text message. Deliberately blunt: the safe
# body is generic, so any clinical vocabulary means something composed a
# message that was not meant for this channel.
_CLINICAL_HINTS = re.compile(
    r"\b(diagnos|symptom|test|result|medication|dose|tablet|pregnan|hiv|sti|"
    r"cancer|mental|depress|specialist|clinic|referral|biopsy|scan|x-ray)\w*",
    re.IGNORECASE,
)


class SmsProvider(Protocol):
    def send(self, phone: str, body: str) -> DeliveryResult:
        ...


class NoopSmsProvider:
    """Records the intent to send without a gateway.

    The default. It keeps the whole path — routing, quiet hours, escalation,
    delivery records — exercisable and testable without an account, and makes
    the eventual integration a config change rather than a redesign.
    """

    name = "noop"

    def send(self, phone: str, body: str) -> DeliveryResult:
        logger.info("SMS (noop) to %s: %s", _mask(phone), body)
        return DeliveryResult(ok=True, status="skipped_no_provider")


def _mask(phone: str) -> str:
    return phone[:-4].rjust(len(phone) - 4, "*") + phone[-4:] if len(phone) > 4 else "****"


def sms_safe_body(message: Message, *, allow_escalation_text: bool = False) -> str:
    """The most that may be said over SMS.

    Returns the generic body unless this is a critical escalation carrying the
    deterministic red-flag wording, and even then re-checks it: the escalation
    strings are fixed constants today, and this stops that quietly changing.
    """
    if not allow_escalation_text:
        return GENERIC_BODY

    candidate = message.body.strip()
    if _CLINICAL_HINTS.search(candidate):
        logger.warning("SMS body contained clinical vocabulary; sending generic text.")
        return GENERIC_BODY
    for label, pattern in _EGRESS_PATTERNS:
        if pattern.search(candidate):
            logger.warning(
                "SMS body contained %s; sending generic text instead.", label
            )
            return GENERIC_BODY
    return candidate[:300]


class SmsChannel:
    name = "sms"

    def __init__(self, provider: SmsProvider | None = None) -> None:
        self.provider = provider or NoopSmsProvider()

    def send(self, db: Session, recipient: User, message: Message) -> DeliveryResult:
        if not recipient.phone:
            return DeliveryResult(ok=False, status="no_phone")

        body = sms_safe_body(
            message,
            allow_escalation_text=(str(message.priority).lower() == "critical"),
        )
        try:
            return self.provider.send(recipient.phone, body)
        except Exception as exc:  # noqa: BLE001 - a gateway failure is not our crash
            logger.exception("SMS provider failed")
            return DeliveryResult(ok=False, status="provider_error", error=repr(exc))


def build_channel() -> SmsChannel:
    """The configured SMS channel, or the no-op one."""
    configured = (getattr(settings, "sms_provider", "") or "").strip().lower()
    if not configured or configured == "noop":
        return SmsChannel(NoopSmsProvider())
    # A real aggregator drops in here. Sri Lankan gateways (Dialog, Mobitel,
    # Text.lk) are all token-authed HTTP POSTs, so the adapter is small — but
    # it is not written speculatively against an API nobody has credentials for.
    logger.warning("Unknown sms_provider %r; falling back to noop.", configured)
    return SmsChannel(NoopSmsProvider())
