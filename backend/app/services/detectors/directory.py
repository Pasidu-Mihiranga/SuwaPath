"""Noticing that the searchable directory has gone stale.

The provider directory is generated from live database rows, so a doctor
verified this morning does not appear in search until someone runs
`python -m app.knowledge.ingest` by hand. In practice nobody remembers, which
means the most common administrative action in the system — verifying a
provider — silently fails to take effect where it matters most.

No new event plumbing is needed to detect this. `AuditLog` already records
every verification and every staff account created, with a timestamp. Compare
the newest such row against when the index was last rebuilt.

The reindex itself runs in the worker, never in a request: it re-embeds every
collection, which is seconds of CPU that an administrator clicking a button
should not wait for.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent import actions
from app.core.db import SessionLocal
from app.models.agentic import ActionProposal, AgentTask
from app.models.enums import UserRole
from app.models.identity import AuditLog
from app.services import clock
from app.services.jobs import register, runner

logger = logging.getLogger(__name__)

TASK_KIND = "directory_reindex"

# Administrative actions that change what the directory should contain.
MUTATING_ACTIONS = ("provider.verify", "user.create", "hospital.capability")


def _last_reindex_at(db: Session):
    """When the directory was last rebuilt through an approved proposal."""
    return db.execute(
        select(func.max(ActionProposal.executed_at)).where(
            ActionProposal.action_name == "reindex_provider_directory",
            ActionProposal.status == "executed",
        )
    ).scalar_one_or_none()


def detect(db: Session) -> int:
    """Queue a reindex proposal when providers changed after the last build."""
    latest_change = db.execute(
        select(func.max(AuditLog.created_at)).where(
            AuditLog.action.in_(MUTATING_ACTIONS)
        )
    ).scalar_one_or_none()

    if latest_change is None:
        return 0

    last_build = _last_reindex_at(db)
    if last_build is not None and last_build >= latest_change:
        return 0  # nothing has changed since the last rebuild

    changed = db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action.in_(MUTATING_ACTIONS),
            AuditLog.created_at > last_build if last_build else True,
        )
    ).scalar_one()

    queued = runner.enqueue(
        db,
        kind=TASK_KIND,
        # Bucketed by hour so a burst of verifications coalesces into one
        # rebuild rather than one per click.
        dedupe_key=f"reindex:{clock.now().strftime('%Y-%m-%dT%H')}",
        payload={"changed_since_last_build": changed},
    )
    return 1 if queued else 0


@runner.handler(TASK_KIND)
def handle(db: Session, task: AgentTask) -> None:
    changed = task.payload.get("changed_since_last_build", 0)
    actions.propose(
        db,
        audience_role=UserRole.SYSTEM_ADMIN,
        action_name="reindex_provider_directory",
        args={},
        title="Rebuild the provider directory?",
        preview_text=(
            f"{changed} provider or facility change(s) have been made since the "
            "search index was last built. Until it is rebuilt, those providers "
            "will not appear in assistant search results or in care matching."
        ),
        evidence={
            "detector_id": "directory_reindex",
            "changed_since_last_build": changed,
        },
        idempotency_key=f"reindex:{clock.now().strftime('%Y-%m-%dT%H')}",
        origin="job",
        ttl_days=3,
    )
    db.commit()


@register(
    "detect_directory_staleness",
    seconds=30 * 60,
    description="Propose a directory rebuild when providers have changed",
)
def job() -> None:
    db = SessionLocal()
    try:
        detect(db)
    finally:
        db.close()
