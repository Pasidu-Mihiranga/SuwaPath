"""Starting the background scheduler.

APScheduler in-process, threads not asyncio: every node and service in this
codebase uses synchronous SQLAlchemy, and `agent_session()` in the agent graph
already opens one short-lived session per worker thread. Running jobs on the
request event loop would block it.

**This is the honest limitation of the design:** the scheduler lives inside the
API process, so it dies with the API. That is acceptable for a single-machine
deployment and is why `app/worker.py` exists — the same registry can be run as
a separate process, and the advisory lock means running both is safe rather
than duplicated.

Not Celery: it needs a broker, and there is no Redis here. Not a job store in
Postgres: the schedule is code (see `jobs/__init__.py`).
"""

from __future__ import annotations

import logging

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings
from app.services.jobs import load_jobs, run_job, runner

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start() -> BackgroundScheduler | None:
    """Start the scheduler unless it is disabled or already running."""
    global _scheduler
    if not (settings.agentic_enabled and settings.scheduler_enabled):
        logger.info("Scheduler disabled by configuration.")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=2)},
        job_defaults={
            # A job that overruns must not stack up behind itself.
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 300,
        },
        timezone="UTC",
    )

    jobs = load_jobs()
    for job in jobs:
        scheduler.add_job(
            run_job,
            "interval",
            seconds=job.interval_seconds,
            args=[job],
            id=job.name,
            name=job.description,
        )

    # The queue drain is itself a job, so detectors and handlers share one
    # mechanism rather than the handlers needing a second scheduler.
    scheduler.add_job(
        _drain,
        "interval",
        seconds=settings.job_tick_seconds,
        id="drain_tasks",
        name="Run queued autonomous tasks",
    )

    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started with %d detector job(s) + task drain every %ds.",
        len(jobs),
        settings.job_tick_seconds,
    )
    return scheduler


def _drain() -> None:
    try:
        ran = runner.drain()
        if ran:
            logger.info("Ran %d autonomous task(s).", ran)
    except Exception:  # noqa: BLE001
        logger.exception("Task drain failed.")


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped.")
