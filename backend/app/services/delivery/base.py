"""Getting a message to a person, and knowing whether it arrived.

A notification row is not a delivery. Until now the two were the same thing:
the system wrote a row and assumed the patient would eventually open the app
and see it. For the population this product is aimed at — someone who has not
booked the follow-up they were told to book — that assumption is exactly
backwards. The people most in need of a reminder are the least likely to be
looking at the screen.

So delivery is modelled explicitly: a channel that can succeed or fail, and a
record of which. That record is what makes "escalate to SMS if still unread in
an hour" expressible, and what makes delivery rate a number rather than a hope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.models.identity import User


@dataclass(frozen=True)
class Message:
    """What to say, and how loudly."""

    title: str
    body: str
    priority: str
    category: str = "system"
    action_type: str | None = None
    action_id: str | None = None
    # Set when the message concerns someone other than the recipient, as with
    # a guardian alert.
    about_patient_user_id: str | None = None


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    status: str
    provider_message_id: str | None = None
    error: str | None = None


class Channel(Protocol):
    """One way of reaching a person."""

    name: str

    def send(self, db: Session, recipient: User, message: Message) -> DeliveryResult:
        ...
