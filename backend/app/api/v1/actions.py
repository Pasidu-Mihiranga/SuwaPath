"""Reviewing and approving what the system proposed.

A proposal is a *record of intent*, never an authorisation. By the time someone
taps Approve, hours or days have passed: the slot may be gone, a guardian's
consent may have been withdrawn, the recommendation may have been resolved
elsewhere. So this endpoint re-derives everything at the moment of execution
and treats the stored arguments as a suggestion about what to attempt.

Concretely, approving re-checks:

- that the approver is the patient or a guardian holding the right scope
- that the action still exists and is still in a tier that may be executed
- every rule inside the action itself, via the same shared service the
  human-facing endpoints call

Trusting `args` would let an action proposed under one consent state execute
under another, which is the whole class of bug this design exists to avoid.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_relationship, require_permission
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.agentic import ActionProposal
from app.models.enums import UserRole
from app.models.identity import User
from app.agent import actions as action_registry
from app.services import clock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/actions", tags=["agent-actions"])


def _serialise(proposal: ActionProposal) -> dict:
    return {
        "id": proposal.id,
        "action": proposal.action_name,
        "risk_tier": proposal.risk_tier,
        "title": proposal.title,
        "preview": proposal.preview_text,
        "status": proposal.status,
        "origin": proposal.origin,
        # Evidence is surfaced rather than hidden: a patient asked to approve
        # something the system decided on its own is entitled to see why.
        "evidence": proposal.evidence,
        "created_at": proposal.created_at,
        "expires_at": proposal.expires_at,
        "executed_at": proposal.executed_at,
        "result": proposal.result,
    }


def _subjects_visible_to(db: Session, user: User) -> list[str]:
    """Whose proposals this person may see."""
    if str(user.role) == str(UserRole.GUARDIAN):
        from app.models.identity import GuardianRelationship

        rows = db.execute(
            select(GuardianRelationship.patient_user_id).where(
                GuardianRelationship.guardian_user_id == user.id,
                GuardianRelationship.is_active.is_(True),
            )
        ).scalars().all()
        return list(rows)
    return [user.id]


@router.get("")
def list_proposals(
    status_filter: str = Query(default="pending", alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    subjects = _subjects_visible_to(db, current_user)
    if not subjects:
        return {"proposals": []}

    stmt = select(ActionProposal).where(ActionProposal.subject_user_id.in_(subjects))
    if status_filter != "all":
        stmt = stmt.where(ActionProposal.status == status_filter)

    proposals = db.execute(
        stmt.order_by(ActionProposal.created_at.desc()).limit(50)
    ).scalars().all()

    # Expiry is applied on read rather than by a sweep: a proposal nobody
    # looked at is harmless, and this keeps the list honest without a job.
    now = clock.now()
    out = []
    for proposal in proposals:
        if (
            proposal.status == "pending"
            and proposal.expires_at
            and proposal.expires_at < now
        ):
            proposal.status = "expired"
        out.append(_serialise(proposal))
    db.commit()
    return {"proposals": out}


def _load_for_decision(
    db: Session, proposal_id: str, current_user: User
) -> tuple[ActionProposal, User]:
    proposal = db.get(ActionProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="That suggestion no longer exists.")

    if proposal.subject_user_id not in _subjects_visible_to(db, current_user):
        # Same answer as a missing row: a 403 here would confirm the proposal
        # exists, and proposals are about someone's health.
        raise HTTPException(status_code=404, detail="That suggestion no longer exists.")

    if proposal.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"That suggestion was already {proposal.status}.",
        )
    if proposal.expires_at and proposal.expires_at < clock.now():
        proposal.status = "expired"
        db.commit()
        raise HTTPException(status_code=409, detail="That suggestion has expired.")

    patient = db.get(User, proposal.subject_user_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return proposal, patient


@router.post("/{proposal_id}/approve")
def approve(
    proposal_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    proposal, patient = _load_for_decision(db, proposal_id, current_user)
    action = action_registry.get(proposal.action_name)

    # Re-derive the guardian's authority now, rather than trusting that it
    # held when the proposal was written.
    if current_user.id != patient.id and action.required_permission is not None:
        relationship = get_relationship(db, current_user.id, patient.id)
        require_permission(relationship, action.required_permission)

    try:
        result = action.run(
            db,
            actor=current_user,
            patient=patient,
            **proposal.args,
        )
    except HTTPException as exc:
        # A stale slot is the expected failure, and a dead end here is exactly
        # the follow-through problem this feature exists to fix — so record it
        # and let the client offer alternatives.
        db.rollback()
        proposal.status = "failed"
        proposal.result = {"error": exc.detail}
        proposal.decided_at = clock.now()
        proposal.decided_by_user_id = current_user.id
        db.commit()
        raise HTTPException(
            status_code=exc.status_code,
            detail=f"{exc.detail} The suggestion has been closed — search again to rebook.",
        ) from exc
    except action_registry.ActionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    proposal.status = "executed"
    proposal.result = result
    proposal.decided_at = clock.now()
    proposal.decided_by_user_id = current_user.id
    proposal.executed_at = clock.now()
    db.commit()

    logger.info(
        "proposal %s (%s) approved by %s", proposal.id, proposal.action_name, current_user.id
    )
    return {"status": "executed", "result": result}


@router.post("/{proposal_id}/reject")
def reject(
    proposal_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    proposal, _ = _load_for_decision(db, proposal_id, current_user)
    proposal.status = "rejected"
    proposal.decided_at = clock.now()
    proposal.decided_by_user_id = current_user.id
    db.commit()
    return {"status": "rejected"}
