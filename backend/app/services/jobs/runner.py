"""Enqueueing, claiming and running autonomous work.

The design separates two things that are easy to conflate:

**Detectors are pure.** They look at the data and enqueue tasks. They never
send anything, never write clinical state, and never act. This is what makes
running them often — every 15 minutes, forever — safe and cheap to reason
about.

**Handlers act.** They run once per task, at most, and their effects are what
the patient sees.

Three independent mechanisms keep that safe when the same code runs twice, in
two processes, or after a crash:

1. A Postgres advisory lock, so only one process runs a given job per tick.
2. A UNIQUE `dedupe_key`, so re-detecting the same situation enqueues nothing.
3. `FOR UPDATE SKIP LOCKED` claiming, so two workers never take one task.

Any one of them failing still leaves the other two.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.agentic import AgentTask, TaskRun
from app.services import clock

logger = logging.getLogger(__name__)

# A task claimed but not finished within this window is assumed to belong to a
# worker that died, and is returned to the queue.
LEASE_MINUTES = 15

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

# kind -> handler. Handlers take (db, task) and raise to fail the attempt.
HANDLERS: dict[str, Callable[[Session, AgentTask], None]] = {}


def handler(kind: str):
    """Register a task handler."""

    def wrap(fn: Callable[[Session, AgentTask], None]):
        HANDLERS[kind] = fn
        return fn

    return wrap


# --------------------------------------------------------------------------
# Cross-process mutual exclusion
# --------------------------------------------------------------------------
@contextmanager
def advisory_lock(db: Session, name: str):
    """Hold a Postgres advisory lock for the duration of the block.

    Session-scoped rather than transaction-scoped, and released in `finally`,
    so a crashed process drops it when its connection closes. There is no
    stale-lock state to clean up, which is the failure mode a lock *table*
    would introduce.

    Yields False when another process holds it — the caller should skip, not
    wait. A tick that is late is fine; a tick that queues up behind another is
    how a scheduler falls over.
    """
    acquired = db.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": f"suwapath:{name}"}
    ).scalar()
    try:
        yield bool(acquired)
    finally:
        if acquired:
            db.execute(
                text("SELECT pg_advisory_unlock(hashtext(:k))"),
                {"k": f"suwapath:{name}"},
            )
            db.commit()


# --------------------------------------------------------------------------
# Enqueue
# --------------------------------------------------------------------------
def enqueue(
    db: Session,
    *,
    kind: str,
    dedupe_key: str,
    subject_user_id: str | None = None,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
    priority: int = 0,
) -> bool:
    """Queue a task unless its intent is already queued.

    Returns True if a new task was created. `ON CONFLICT DO NOTHING` means the
    caller does not need to check first, and two processes racing on the same
    detection produce one task rather than an integrity error.
    """
    stmt = (
        pg_insert(AgentTask)
        .values(
            kind=kind,
            dedupe_key=dedupe_key,
            subject_user_id=subject_user_id,
            payload=payload or {},
            run_after=run_after or clock.now(),
            priority=priority,
            status="queued",
            max_attempts=settings.task_max_attempts,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(AgentTask.id)
    )
    created = db.execute(stmt).scalar_one_or_none()
    db.commit()
    if created:
        logger.info("task queued kind=%s key=%s", kind, dedupe_key)
    return created is not None


# --------------------------------------------------------------------------
# Claim and run
# --------------------------------------------------------------------------
def claim_tasks(db: Session, *, limit: int = 10) -> list[AgentTask]:
    """Atomically take up to `limit` due tasks for this worker.

    `SKIP LOCKED` is what allows several workers to drain the same queue
    without coordinating: a row another transaction has locked is passed over
    rather than waited for.
    """
    rows = db.execute(
        text(
            """
            UPDATE agent_tasks SET
                status = 'running',
                claimed_by = :worker,
                claimed_at = :now,
                attempts = attempts + 1
            WHERE id IN (
                SELECT id FROM agent_tasks
                WHERE status = 'queued' AND (run_after IS NULL OR run_after <= :now)
                ORDER BY priority DESC, run_after ASC
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id
            """
        ),
        {"worker": WORKER_ID, "now": clock.now(), "limit": limit},
    ).scalars().all()
    db.commit()
    if not rows:
        return []
    return list(db.query(AgentTask).filter(AgentTask.id.in_(rows)).all())


def _finish(db: Session, task: AgentTask, *, status: str, detail: str | None, ms: int) -> None:
    db.add(
        TaskRun(
            task_id=task.id,
            attempt=task.attempts,
            status="ok" if status == "done" else "error",
            duration_ms=ms,
            detail=detail,
        )
    )
    task.status = status
    task.last_error = None if status == "done" else detail
    if status in {"done", "failed"}:
        task.completed_at = clock.now()
    db.commit()


def run_task(db: Session, task: AgentTask) -> None:
    """Run one claimed task, recording the attempt either way."""
    started = time.monotonic()
    fn = HANDLERS.get(task.kind)
    if fn is None:
        _finish(db, task, status="failed", detail=f"no handler for {task.kind!r}", ms=0)
        return

    try:
        fn(db, task)
    except Exception as exc:  # noqa: BLE001 - one task must not stop the queue
        db.rollback()
        ms = int((time.monotonic() - started) * 1000)
        exhausted = task.attempts >= task.max_attempts
        if exhausted:
            _finish(db, task, status="failed", detail=repr(exc), ms=ms)
            logger.exception("task %s failed permanently", task.kind)
        else:
            # Back off so a transient failure (a provider down, a lock held)
            # is retried later rather than hammered.
            task.status = "queued"
            task.run_after = clock.now() + timedelta(minutes=2 ** task.attempts)
            _finish_retry(db, task, detail=repr(exc), ms=ms)
            logger.warning("task %s attempt %s failed: %s", task.kind, task.attempts, exc)
        return

    _finish(db, task, status="done", detail=None, ms=int((time.monotonic() - started) * 1000))


def _finish_retry(db: Session, task: AgentTask, *, detail: str, ms: int) -> None:
    db.add(
        TaskRun(task_id=task.id, attempt=task.attempts, status="error", duration_ms=ms, detail=detail)
    )
    task.last_error = detail
    db.commit()


def recover_stalled(db: Session) -> int:
    """Return tasks abandoned by a dead worker to the queue."""
    cutoff = clock.now() - timedelta(minutes=LEASE_MINUTES)
    stalled = (
        db.query(AgentTask)
        .filter(AgentTask.status == "running", AgentTask.claimed_at < cutoff)
        .all()
    )
    for task in stalled:
        task.status = "queued" if task.attempts < task.max_attempts else "failed"
        task.claimed_by = None
    if stalled:
        db.commit()
        logger.info("recovered %d stalled task(s)", len(stalled))
    return len(stalled)


def drain(*, limit: int = 25) -> int:
    """Run every due task. Returns how many ran.

    Opens its own session: this is called from a scheduler thread, not from a
    request, so there is no `get_db` dependency to inherit.
    """
    db = SessionLocal()
    try:
        recover_stalled(db)
        ran = 0
        while ran < limit:
            batch = claim_tasks(db, limit=min(10, limit - ran))
            if not batch:
                break
            for task in batch:
                run_task(db, task)
                ran += 1
        return ran
    finally:
        db.close()
