"""In-app delivery: writing the notification the client already reads.

Deliberately the simplest channel, and the only one that carries clinical
detail. The app is behind authentication, so it is the one place where saying
what the message is actually about is safe.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.identity import User
from app.models.platform import Notification
from app.services.delivery.base import DeliveryResult, Message


class InAppChannel:
    name = "in_app"

    def send(self, db: Session, recipient: User, message: Message) -> DeliveryResult:
        notification = Notification(
            user_id=recipient.id,
            category=message.category,
            priority=message.priority,
            title=message.title[:160],
            body=message.body,
            action_type=message.action_type,
            action_id=message.action_id,
            about_patient_user_id=message.about_patient_user_id,
        )
        db.add(notification)
        db.flush()
        return DeliveryResult(ok=True, status="delivered", provider_message_id=notification.id)
