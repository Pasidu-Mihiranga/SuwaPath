"""Actions: the point at which the system stops answering and starts doing.

Every tool in `tools.py` is a read. This module is the other half — the things
that change something — and it is deliberately a separate registry rather than
more entries in the same table, because reads and writes need different rules
and mixing them makes the difference easy to lose.

The risk ladder
---------------
Not every action deserves the same ceremony. Asking permission to set a
reminder trains people to tap Approve without reading, which is worse than not
asking. Booking a real clinic slot on someone's behalf without asking is a
different matter.

**T0 — executes immediately.** Reversible, cheap to get wrong, and annoying to
be asked about. Still audited.

**T1 — prepared in full, executed on one tap.** The system does all the work —
picks the doctor, finds the slot, computes the fee — and the human supplies
only the decision.

**T2 — never automated.** Not "requires approval": absent from the registry
entirely, so there is no path from a model's output to this happening.
Prescribing, diagnosing, writing clinical notes, setting urgency, and changing
consent live here. Urgency in particular is decided by the deterministic
red-flag engine, and that guarantee is the project's core safety claim.

Two rules hold across all tiers
-------------------------------
**Confidential sessions cannot act at all.** Private mode promises that
nothing is written down; an action taken from one would break that promise in
a way the user cannot see. The gate is checked before the registry is even
consulted.

**Alerts require evidence.** `raise_guardian_alert` refuses to run without a
`rule_id` or `detector_id` naming the deterministic check that fired. A model
can compose the wording of an alert; it cannot invent the grounds for one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.enums import (
    GuardianPermissionType,
    NotificationCategory,
    NotificationPriority,
    VisitType,
)
from app.models.identity import User
from app.models.platform import Notification
from app.services import clock

logger = logging.getLogger(__name__)


class ActionError(Exception):
    """An action that cannot be performed as asked."""


@dataclass(frozen=True)
class Action:
    name: str
    tier: str  # "T0" | "T1"
    summary: str
    run: Callable[..., dict[str, Any]]
    required_permission: GuardianPermissionType | None = None


ACTIONS: dict[str, Action] = {}


def register(
    name: str,
    *,
    tier: str,
    summary: str,
    permission: GuardianPermissionType | None = None,
):
    def wrap(fn: Callable[..., dict[str, Any]]):
        ACTIONS[name] = Action(
            name=name, tier=tier, summary=summary, run=fn, required_permission=permission
        )
        return fn

    return wrap


def get(name: str) -> Action:
    action = ACTIONS.get(name)
    if action is None:
        # Covers both a typo and a model naming a T2 action that was never
        # registered. Same answer either way: it does not exist.
        raise ActionError(f"Unknown action {name!r}.")
    return action


# --------------------------------------------------------------------------
# T0 — immediate
# --------------------------------------------------------------------------
@register(
    "schedule_reminder",
    tier="T0",
    summary="Put a reminder in the patient's notifications",
)
def schedule_reminder(
    db: Session,
    *,
    patient: User,
    title: str,
    body: str,
    when: datetime | None = None,
    category: NotificationCategory = NotificationCategory.FOLLOW_UP,
    **_: Any,
) -> dict[str, Any]:
    # The category comes from the caller, which always knows what the reminder
    # is about. Adding a catch-all `REMINDER` member instead would have been
    # easier and wrong: the notifications UI filters by category, so a bucket
    # that means "something" makes every other filter incomplete.
    try:
        category = NotificationCategory(str(category))
    except ValueError as exc:
        raise ActionError(f"Unknown notification category {category!r}.") from exc

    notification = Notification(
        user_id=patient.id,
        category=category,
        priority=NotificationPriority.NORMAL,
        title=title[:160],
        body=body,
        scheduled_for=when,
    )
    db.add(notification)
    db.flush()
    return {"notification_id": notification.id}


@register(
    "raise_guardian_alert",
    tier="T0",
    summary="Alert guardians who hold the required consent scope",
    permission=GuardianPermissionType.FULL_MEDICAL,
)
def raise_alert(
    db: Session,
    *,
    patient: User,
    alert_type: str,
    severity: Any,
    title: str,
    detail: str,
    permission: GuardianPermissionType,
    evidence: dict | None = None,
    **_: Any,
) -> dict[str, Any]:
    # The whole point of this guard: an alert must trace back to a
    # deterministic check, never to a model's impression.
    if not evidence or not (evidence.get("rule_id") or evidence.get("detector_id")):
        raise ActionError(
            "A guardian alert needs evidence naming the rule or detector that fired."
        )

    from app.services.alerts import raise_guardian_alerts

    raised = raise_guardian_alerts(
        db,
        patient=patient,
        alert_type=alert_type,
        severity=severity,
        title=title,
        detail=detail,
        permission=permission,
        meta={"evidence": evidence},
    )
    return {"guardians_alerted": raised}


# --------------------------------------------------------------------------
# T1 — prepared, then approved
# --------------------------------------------------------------------------
@register(
    "book_appointment",
    tier="T1",
    summary="Book a specific slot with a specific doctor",
    permission=GuardianPermissionType.APPOINTMENTS,
)
def book_appointment(
    db: Session,
    *,
    actor: User,
    patient: User,
    doctor_id: str,
    scheduled_start: datetime | str,
    visit_type: str = "physical",
    reason: str | None = None,
    recommendation_id: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Execute a booking through the one shared code path.

    Deliberately thin. Every permission and availability check lives in
    `services/booking.py`, which the human-facing endpoint also calls, so an
    approved proposal cannot take a more permissive route than a person tapping
    Book would.
    """
    from app.services import booking

    start = (
        datetime.fromisoformat(scheduled_start)
        if isinstance(scheduled_start, str)
        else scheduled_start
    )
    appointment = booking.create_appointment(
        db,
        actor=actor,
        patient=patient,
        doctor_id=doctor_id,
        scheduled_start=start,
        visit_type=VisitType(visit_type),
        reason=reason,
        recommendation_id=recommendation_id,
        commit=False,
    )
    return {
        "appointment_id": appointment.id,
        "scheduled_start": appointment.scheduled_start.isoformat(),
    }


# --------------------------------------------------------------------------
# Proposals
# --------------------------------------------------------------------------
DEFAULT_PROPOSAL_TTL_DAYS = 14


def propose(
    db: Session,
    *,
    subject: User,
    action_name: str,
    args: dict[str, Any],
    title: str,
    preview_text: str,
    evidence: dict[str, Any],
    idempotency_key: str,
    origin: str = "job",
    origin_ref: str | None = None,
    ttl_days: int = DEFAULT_PROPOSAL_TTL_DAYS,
) -> str | None:
    """Record an action awaiting approval. Returns its id, or None if it exists.

    Uniqueness on `idempotency_key` is what stops a detector that runs daily
    from filling someone's inbox with the same suggestion.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.agentic import ActionProposal

    action = get(action_name)
    stmt = (
        pg_insert(ActionProposal)
        .values(
            subject_user_id=subject.id,
            origin=origin,
            origin_ref=origin_ref,
            action_name=action_name,
            args=args,
            risk_tier=action.tier,
            title=title[:160],
            preview_text=preview_text,
            evidence=evidence,
            idempotency_key=idempotency_key,
            status="pending",
            expires_at=clock.now() + timedelta(days=ttl_days),
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(ActionProposal.id)
    )
    proposal_id = db.execute(stmt).scalar_one_or_none()
    db.commit()
    return proposal_id
