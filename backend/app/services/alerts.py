"""Raising alerts to the people a patient authorised.

Extracted from the care router so that background jobs can call it. It never
needed a request — it takes a plain `Session` and a `User` — but importing it
from an API module pulls FastAPI's dependency graph into a scheduler thread,
which is how a job ends up needing a request context it does not have.

The consent check is the load-bearing part: a relationship grants nothing on
its own, and each alert names the scope it requires. An autonomous alert is
subject to exactly the same rule as one a human triggered, which is the
property that makes the autonomy layer safe to switch on.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import (
    AlertSeverity,
    GuardianPermissionType,
    NotificationCategory,
    NotificationPriority,
)
from app.models.identity import GuardianRelationship, User
from app.models.platform import GuardianAlert, Notification


# --------------------------------------------------------------------------
# Guardian alert helper (consent-aware)
# --------------------------------------------------------------------------
def raise_guardian_alerts(
    db: Session,
    *,
    patient: User,
    alert_type: str,
    severity: AlertSeverity,
    title: str,
    detail: str,
    permission: GuardianPermissionType,
    meta: dict | None = None,
) -> int:
    """Notify guardians who hold the required consent scope (rule 6)."""
    relationships = db.execute(
        select(GuardianRelationship)
        .options(selectinload(GuardianRelationship.permissions))
        .where(
            GuardianRelationship.patient_user_id == patient.id,
            GuardianRelationship.is_active.is_(True),
        )
    ).scalars().unique().all()

    raised = 0
    for relationship in relationships:
        granted = relationship.granted_scopes()
        if not (
            permission in granted or GuardianPermissionType.FULL_MEDICAL in granted
        ):
            continue

        db.add(
            GuardianAlert(
                patient_user_id=patient.id,
                guardian_user_id=relationship.guardian_user_id,
                alert_type=alert_type,
                severity=severity,
                title=title,
                detail=detail,
                required_permission=str(permission),
                meta=meta or {},
            )
        )
        db.add(
            Notification(
                user_id=relationship.guardian_user_id,
                category=NotificationCategory.GUARDIAN_ALERT,
                priority=(
                    NotificationPriority.CRITICAL
                    if severity == AlertSeverity.CRITICAL
                    else NotificationPriority.HIGH
                ),
                title=title,
                body=detail,
                about_patient_user_id=patient.id,
                action_type="guardian_alert",
            )
        )
        raised += 1
    return raised
