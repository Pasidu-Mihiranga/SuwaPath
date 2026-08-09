"""The job registry: what runs on its own, and how often.

The schedule is code rather than rows in a table. A schedule stored in the
database drifts away from the handlers it names — someone deletes a detector
and a job row is left pointing at nothing. Declaring it here means the two
cannot disagree, and the whole autonomous surface of the product is one
readable list.

Durability lives in `agent_tasks` instead: the *work* survives restarts, the
*timetable* is rebuilt from this file every boot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from app.core.db import SessionLocal
from app.services.jobs import runner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    name: str
    interval_seconds: int
    fn: Callable[[], None]
    description: str


JOBS: list[Job] = []


def register(name: str, *, seconds: int, description: str):
    def wrap(fn: Callable[[], None]):
        JOBS.append(Job(name=name, interval_seconds=seconds, fn=fn, description=description))
        return fn

    return wrap


def run_job(job: Job) -> None:
    """Run one job under an advisory lock, swallowing its failures.

    A detector that throws must not take the scheduler thread down with it, or
    one bad patient record silently stops all autonomy.
    """
    db = SessionLocal()
    try:
        with runner.advisory_lock(db, f"job:{job.name}") as acquired:
            if not acquired:
                logger.debug("job %s already running elsewhere", job.name)
                return
            job.fn()
    except Exception:  # noqa: BLE001
        logger.exception("job %s failed", job.name)
    finally:
        db.close()


# Registering the detectors imports them for their side effect. Kept at the
# bottom so the decorators above are defined first.
def load_jobs() -> list[Job]:
    from app.services import detectors  # noqa: F401  registers jobs + handlers

    return JOBS
