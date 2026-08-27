"""Append-only, tamper-evident audit logging.

The audit table already existed and recorded six kinds of event. It was an
ordinary table: anyone able to reach the database could edit a row or delete
one, and nothing would ever indicate it had happened. For a system holding
medical records that is the wrong property — the value of an audit trail is
almost entirely in being able to say afterwards, credibly, what did and did
not occur.

Each entry commits to its predecessor:

    entry_hash = sha256(prev_hash || canonical(entry))

Change a field, remove a row, or reorder history, and every subsequent hash
stops matching. It cannot *prevent* tampering — a database administrator can
still rewrite rows, and recompute the chain if they understand it — but it
converts silent alteration into something that has to be done deliberately and
completely, and that fails an audit if it is not.

Concurrency is the part that is easy to get wrong. Two requests appending at
the same moment must not both read the same tip and write two "next" entries
that each point at it; the chain would fork and both halves would verify in
isolation. The tip therefore lives in its own single-row table and is taken
with `SELECT ... FOR UPDATE`, which serialises appends. Audit writes are not a
hot path, so the cost is acceptable and the alternative is not.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base import new_uuid
from app.models.identity import AuditChainHead, AuditLog

logger = logging.getLogger(__name__)

GENESIS = "0" * 64


def _canonical(payload: dict) -> str:
    """A stable string for a payload, so the same entry always hashes alike.

    `sort_keys` matters: Python preserves insertion order, so two dicts with
    identical contents built in different orders would otherwise produce
    different hashes and break verification for reasons unrelated to tampering.
    """
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def compute_hash(prev_hash: str, payload: dict) -> str:
    return hashlib.sha256(f"{prev_hash}{_canonical(payload)}".encode()).hexdigest()


def _stamp(moment: datetime | None) -> str | None:
    """A timestamp string that survives a database round trip unchanged.

    The same instant comes back from Postgres rendered in the session's
    timezone — written as `...T13:37:29+00:00`, re-read as `...T19:07:29+05:30`.
    Identical moment, different string, different hash. Left alone, every
    entry failed verification the instant it was read back, which would have
    made the chain look like it was detecting constant tampering while
    actually detecting nothing at all.

    Normalising to UTC makes the representation canonical. A naive datetime is
    assumed to be UTC, which is what the application writes.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def entry_payload(entry: AuditLog) -> dict:
    """The fields the hash covers.

    `created_at` is included so an entry cannot be silently re-dated, and `id`
    so one cannot be swapped for another. Anything not listed here is outside
    the chain's protection, which is why it is written out explicitly rather
    than derived from the model's columns — a column added later should not
    quietly change what every historical hash was computed over.
    """
    return {
        "id": entry.id,
        "actor_user_id": entry.actor_user_id,
        "actor_role": entry.actor_role,
        "action": entry.action,
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "detail": entry.detail or {},
        "ip_address": entry.ip_address,
        "created_at": _stamp(entry.created_at),
    }


def _head(db: Session, *, lock: bool) -> AuditChainHead:
    statement = select(AuditChainHead).where(AuditChainHead.id == "head")
    if lock:
        statement = statement.with_for_update()
    head = db.execute(statement).scalar_one_or_none()
    if head is None:
        head = AuditChainHead(id="head", last_hash=GENESIS, entries=0)
        db.add(head)
        db.flush()
    return head


def record(
    db: Session,
    *,
    action: str,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
    commit: bool = True,
) -> AuditLog:
    """Append one entry to the chain.

    The caller's transaction is used, so an audit entry and the change it
    records commit together or not at all. `commit=False` is for callers that
    manage their own transaction boundary.
    """
    head = _head(db, lock=True)

    # The id is assigned here rather than left to the column default. That
    # default runs at flush, so `entry.id` is still None while the hash is
    # being computed — the chain then commits to `"id": null` and every entry
    # fails verification the moment it is read back with its real id. A hash
    # chain that never verifies is indistinguishable from one that has been
    # tampered with, which is a worse outcome than having no chain at all.
    entry = AuditLog(
        id=new_uuid(),
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail or {},
        ip_address=ip_address,
        prev_hash=head.last_hash,
    )
    # created_at is part of the hash, so it has to exist before hashing rather
    # than being filled in by the database default afterwards.
    entry.created_at = entry.created_at or datetime.now(timezone.utc)
    entry.entry_hash = compute_hash(head.last_hash, entry_payload(entry))

    db.add(entry)
    head.last_hash = entry.entry_hash
    head.entries = (head.entries or 0) + 1

    if commit:
        db.commit()
    return entry


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------
def verify(db: Session, *, limit: int | None = None) -> dict:
    """Walk the chain and report the first place it stops adding up.

    Returns a summary rather than raising, because the interesting outcome of
    a verification run is a report, not an exception. Entries written before
    the chain existed carry no hash and are counted separately — they are
    unverifiable rather than invalid, and conflating the two would make the
    upgrade itself look like tampering.
    """
    statement = select(AuditLog).order_by(AuditLog.created_at, AuditLog.id)
    if limit:
        statement = statement.limit(limit)
    entries = list(db.execute(statement).scalars())

    previous = GENESIS
    checked = 0
    unchained = 0
    problems: list[dict] = []

    for entry in entries:
        if not entry.entry_hash:
            unchained += 1
            continue

        expected = compute_hash(entry.prev_hash or GENESIS, entry_payload(entry))
        if expected != entry.entry_hash:
            problems.append({
                "id": entry.id,
                "action": entry.action,
                "issue": "content does not match its hash — the row was altered",
            })
        elif checked and entry.prev_hash != previous:
            problems.append({
                "id": entry.id,
                "action": entry.action,
                "issue": "does not follow the previous entry — a row was removed or reordered",
            })
        previous = entry.entry_hash
        checked += 1

    return {
        "entries": len(entries),
        "verified": checked,
        "unchained": unchained,
        "intact": not problems,
        "problems": problems,
    }
