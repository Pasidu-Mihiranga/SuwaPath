"""Reviewing and approving what the system proposed.

A proposal is a *record of intent*, never an authorisation. By the time someone
taps Approve, hours or days have passed: the slot may be gone, a guardian's
consent may have been withdrawn, the recommendation may have been resolved
elsewhere. So this endpoint re-derives everything at the moment of execution
and treats the stored arguments as a suggestion about what to attempt.

Concretely, approving re-checks:

- that the approver still holds whatever authority the proposal was addressed
  to — the patient themselves, a guardian with the required consent scope, a
  doctor still treating the patient, or someone who still administers the
  hospital a role claim was scoped to
- that the action still exists and is still in a tier that may be executed
- every rule inside the action itself, via the same shared service the
  human-facing endpoints call

Trusting `args` would let an action proposed under one consent state execute
under another, which is the whole class of bug this design exists to avoid.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.deps import doctor_has_patient, get_relationship, require_permission
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


def _visible_filter(db: Session, user: User):
    """A SQLAlchemy clause for the proposals this person may see.

    Deliberately a filter rather than a list of subject ids, because a
    proposal can now be addressed to a *role claim* — "whoever administers
    hospital X" — which no list of user ids can express.

    The rule per role, and why:

    - **patient**: addressed to them, or about them with no other audience.
    - **guardian**: their dependents' proposals, as before, plus anything
      addressed to them personally.
    - **doctor**: only what is addressed to them. Never by subject — a doctor
      who could see proposals about their patients would see suggestions the
      patient has not acted on and may never share.
    - **hospital_admin**: role claims scoped to their own hospital.
    - **system_admin**: role claims addressed to system administrators, and
      nothing else. The tempting line here is "return everything"; that would
      make every patient's care suggestions readable by an operator, which is
      a PHI hole with an administrative excuse.
    """
    role = str(user.role)

    addressed_to_me = ActionProposal.audience_user_id == user.id

    if role == str(UserRole.PATIENT):
        return or_(
            addressed_to_me,
            and_(
                ActionProposal.audience_user_id.is_(None),
                ActionProposal.audience_role.is_(None),
                ActionProposal.subject_user_id == user.id,
            ),
        )

    if role == str(UserRole.GUARDIAN):
        from app.models.identity import GuardianRelationship

        dependents = select(GuardianRelationship.patient_user_id).where(
            GuardianRelationship.guardian_user_id == user.id,
            GuardianRelationship.is_active.is_(True),
        )
        return or_(
            addressed_to_me,
            and_(
                ActionProposal.audience_user_id.is_(None),
                ActionProposal.audience_role.is_(None),
                ActionProposal.subject_user_id.in_(dependents),
            ),
        )

    if role == str(UserRole.HOSPITAL_ADMIN):
        return or_(
            addressed_to_me,
            and_(
                ActionProposal.audience_role == str(UserRole.HOSPITAL_ADMIN),
                ActionProposal.audience_scope_id == user.hospital_id,
            ),
        )

    if role == str(UserRole.SYSTEM_ADMIN):
        return or_(
            addressed_to_me,
            ActionProposal.audience_role == str(UserRole.SYSTEM_ADMIN),
        )

    # doctor, and anything unrecognised: addressed only.
    return addressed_to_me


@router.get("")
def list_proposals(
    status_filter: str = Query(default="pending", alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(ActionProposal).where(_visible_filter(db, current_user))
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
) -> tuple[ActionProposal, User | None]:
    proposal = db.get(ActionProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="That suggestion no longer exists.")

    visible = db.execute(
        select(ActionProposal.id).where(
            ActionProposal.id == proposal_id, _visible_filter(db, current_user)
        )
    ).scalar_one_or_none()
    if visible is None:
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

    # Nullable now: an operational proposal is about a hospital, not a person.
    patient = (
        db.get(User, proposal.subject_user_id) if proposal.subject_user_id else None
    )
    if proposal.subject_user_id and patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return proposal, patient


def _authorise(
    db: Session,
    approver: User,
    proposal: ActionProposal,
    patient: User | None,
    action,
) -> None:
    """Re-derive the approver's authority at the moment of execution.

    Never inherited from whoever proposed the action, and never taken from the
    stored arguments: consent can be withdrawn, staff move hospitals, and a
    proposal may sit unread for days.

    This used to assume any approver who was not the patient must be a
    guardian, which meant a doctor or an administrator approving anything
    addressed to them was told "You are not registered as a guardian for this
    person" — every cross-role edge was unreachable.
    """
    role = str(approver.role)

    # A role claim: the approver must currently hold that role, and for a
    # hospital-scoped claim must still administer that hospital.
    if proposal.audience_role:
        if role != proposal.audience_role:
            raise HTTPException(
                status_code=403, detail="This suggestion is for a different role."
            )
        if (
            proposal.audience_scope_id
            and getattr(approver, "hospital_id", None) != proposal.audience_scope_id
        ):
            raise HTTPException(
                status_code=403,
                detail="This suggestion belongs to a different hospital.",
            )
        return

    # Addressed to a specific person.
    if proposal.audience_user_id:
        if proposal.audience_user_id != approver.id:
            raise HTTPException(
                status_code=403, detail="This suggestion is addressed to someone else."
            )
        # A doctor acting on a patient's behalf still needs a live clinical
        # relationship — being the addressee is not itself authority over the
        # patient's record.
        if (
            patient is not None
            and role == str(UserRole.DOCTOR)
            and patient.id != approver.id
            and not doctor_has_patient(db, approver.id, patient.id)
        ):
            raise HTTPException(
                status_code=403,
                detail="You are not currently treating this patient.",
            )
        return

    # No explicit audience: the subject decides, or a guardian holding the
    # scope the action requires.
    if patient is not None and approver.id != patient.id:
        if action.required_permission is None:
            raise HTTPException(
                status_code=403, detail="Only this patient can approve that."
            )
        relationship = get_relationship(db, approver.id, patient.id)
        require_permission(relationship, action.required_permission)


@router.post("/{proposal_id}/approve")
def approve(
    proposal_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    proposal, patient = _load_for_decision(db, proposal_id, current_user)
    action = action_registry.get(proposal.action_name)

    if action.requires_subject and patient is None:
        # The proposal names an action that operates on a person, but no
        # subject survived. Better a clear refusal than an AttributeError
        # surfacing as a 500 from inside the action.
        raise HTTPException(
            status_code=409,
            detail="That suggestion is no longer linked to a patient.",
        )

    _authorise(db, current_user, proposal, patient, action)

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
