"""Choosing how loudly to speak.

The channel is picked by a fixed table, not by a model. Waking someone at
02:00 is a decision with a cost, and the rule for when that is justified
belongs in code a person can read and argue with — the same reason urgency
itself is decided by the deterministic red-flag engine rather than an LLM.

The table:

| priority  | channels                                   | quiet hours |
|-----------|--------------------------------------------|-------------|
| critical  | in-app + SMS now, retry SMS at +10 min     | ignored     |
| high      | in-app now, SMS at +60 min if still unread | deferred    |
| normal    | in-app only                                | respected   |
| low       | in-app only                                | respected   |

Escalation is not special-cased here: "SMS if still unread in an hour" is
queued as an ordinary `AgentTask`, so the same substrate that runs the
detectors runs the follow-up. A feature that needs its own scheduler is a
feature that will drift from the rest of the system.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models.agentic import AgentTask, DeliveryAttempt
from app.models.identity import User
from app.models.platform import Notification
from app.services import clock
from app.services.delivery.base import Message
from app.services.delivery.in_app import InAppChannel
from app.services.delivery.sms import build_channel as build_sms
from app.services.jobs import runner

logger = logging.getLogger(__name__)

TASK_KIND = "delivery_escalation"

QUIET_START_HOUR = 21
QUIET_END_HOUR = 7

# Minutes to wait before escalating an unread message to SMS.
ESCALATE_AFTER = {"critical": 10, "high": 60}


def in_quiet_hours() -> bool:
    hour = clock.local_now().hour
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


def _record(
    db: Session,
    *,
    notification_id: str | None,
    recipient: User,
    channel: str,
    result,
) -> None:
    db.add(
        DeliveryAttempt(
            notification_id=notification_id,
            recipient_user_id=recipient.id,
            channel=channel,
            status=result.status,
            provider_message_id=result.provider_message_id,
            error=result.error,
        )
    )


def deliver(db: Session, recipient: User, message: Message) -> dict:
    """Send a message and record what happened on each channel."""
    priority = str(message.priority).lower()

    in_app = InAppChannel().send(db, recipient, message)
    _record(
        db,
        notification_id=in_app.provider_message_id,
        recipient=recipient,
        channel="in_app",
        result=in_app,
    )
    outcome = {"in_app": in_app.status}

    if priority == "critical":
        # The one case that overrides quiet hours. If this is wrong, someone
        # was woken unnecessarily; if the opposite is wrong, nobody was woken
        # when it mattered.
        sms = build_sms().send(db, recipient, message)
        _record(
            db,
            notification_id=in_app.provider_message_id,
            recipient=recipient,
            channel="sms",
            result=sms,
        )
        outcome["sms"] = sms.status

    delay = ESCALATE_AFTER.get(priority)
    if delay and in_app.provider_message_id:
        run_after = clock.now() + timedelta(minutes=delay)
        if priority != "critical" and in_quiet_hours():
            # Hold non-critical escalations until morning rather than dropping
            # them: the message still matters, the hour does not.
            local = clock.local_now()
            morning = local.replace(
                hour=QUIET_END_HOUR, minute=0, second=0, microsecond=0
            )
            if local.hour >= QUIET_START_HOUR:
                morning += timedelta(days=1)
            run_after = morning.astimezone(clock.now().tzinfo)

        runner.enqueue(
            db,
            kind=TASK_KIND,
            dedupe_key=f"escalate:{in_app.provider_message_id}",
            subject_user_id=recipient.id,
            payload={"notification_id": in_app.provider_message_id},
            run_after=run_after,
        )

    db.commit()
    return outcome


@runner.handler(TASK_KIND)
def handle_escalation(db: Session, task: AgentTask) -> None:
    """Send an SMS only if the in-app message is still unread."""
    notification = db.get(Notification, task.payload.get("notification_id"))
    if notification is None or notification.is_read:
        return  # they saw it; nothing more is needed

    recipient = db.get(User, notification.user_id)
    if recipient is None:
        return

    message = Message(
        title=notification.title,
        body=notification.body,
        priority=str(notification.priority),
        category=str(notification.category),
    )
    result = build_sms().send(db, recipient, message)
    _record(
        db,
        notification_id=notification.id,
        recipient=recipient,
        channel="sms",
        result=result,
    )
    db.commit()
