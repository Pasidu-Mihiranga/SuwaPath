"""Turning a no-show prediction into something that happens.

The model has always worked. `predict_no_shows` fits a logistic regression
over nine features, scores every upcoming appointment, bands the risk and
writes `NoShowPrediction` rows. Then nothing reads them. The hospital
dashboard recomputes the numbers on demand, renders a table of names, and
prints the sentence "Consider reminder calls or releasing capacity" — with no
button to do either.

That is the whole "decorative ML" criticism in one screen: a real model whose
output is a paragraph of advice to a human who then has to act by hand.

This detector closes the loop. Once a day it asks the model who is unlikely to
attend tomorrow and proposes one batched reminder to the hospital's
administrator. Approving it sends the reminders and — the part that matters
beyond convenience — **increments `Appointment.reminder_sent_count`**, the
model's own ninth feature, which has been a constant zero since the day it was
written. A feature that never varies teaches a model nothing; this is what
starts it varying.

One batch per hospital per day, deliberately. Twenty individual proposals is
the alert-fatigue failure that the per-patient cap in the referral detector
exists to prevent, and an administrator with twenty approve buttons approves
none of them.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import actions
from app.core.db import SessionLocal
from app.models.agentic import AgentTask
from app.models.enums import UserRole
from app.models.providers import Hospital
from app.services import clock
from app.services.jobs import register, runner

logger = logging.getLogger(__name__)

TASK_KIND = "noshow_reminder_batch"

# Only tomorrow's appointments. A reminder a week out is forgotten; one sent
# the same morning is too late to rearrange a day around.
LOOKAHEAD_DAYS = 2

# Below this, a batch is not worth an administrator's attention.
MIN_BATCH = 3

# How many of tomorrow's riskiest appointments to put in one batch.
BATCH_SIZE = 15

# Selection is by rank, not by risk band, and that is deliberate.
#
# `_band` marks "high" at max(0.35, 2 x base_rate). With a base rate of 0.23
# that threshold is 0.455, while the highest probability this model actually
# produces is around 0.36 — so the high band is unreachable and selecting on
# it would mean this detector never fires. (The same arithmetic empties the
# "high risk" table on the hospital dashboard, which is worth fixing
# separately; `_band`'s own docstring warns about this failure mode.)
#
# Ranking sidesteps it. An administrator does not want "everyone above an
# absolute threshold" anyway — they want the dozen people most worth a call
# tomorrow, which is well defined whatever the base rate does.
MIN_PROBABILITY = 0.20


def detect(db: Session) -> int:
    """Propose one reminder batch per hospital with enough high-risk load."""
    from app.services.analytics import predict_no_shows

    queued = 0
    hospitals = db.execute(select(Hospital)).scalars().all()

    for hospital in hospitals:
        try:
            predictions = predict_no_shows(
                db, hospital_id=hospital.id, days_ahead=LOOKAHEAD_DAYS, persist=True
            )
        except Exception:  # noqa: BLE001 - one hospital must not stop the rest
            logger.exception("No-show scoring failed for hospital %s", hospital.id)
            continue

        ranked = sorted(
            (p for p in predictions if p.get("probability", 0) >= MIN_PROBABILITY),
            key=lambda p: p.get("probability", 0),
            reverse=True,
        )
        high_risk = ranked[:BATCH_SIZE]
        if len(high_risk) < MIN_BATCH:
            continue

        if runner.enqueue(
            db,
            kind=TASK_KIND,
            # One per hospital per local day.
            dedupe_key=f"noshow_batch:{hospital.id}:{clock.today().isoformat()}",
            payload={
                "hospital_id": hospital.id,
                "appointment_ids": [p["appointment_id"] for p in high_risk],
                "top_probability": round(high_risk[0]["probability"], 3),
            },
        ):
            queued += 1

    return queued


@runner.handler(TASK_KIND)
def handle(db: Session, task: AgentTask) -> None:
    hospital = db.get(Hospital, task.payload.get("hospital_id"))
    if hospital is None:
        return

    appointment_ids = task.payload.get("appointment_ids", [])
    if not appointment_ids:
        return

    # A fifth are held back as a control, so the effect of reminding can be
    # measured rather than assumed. Say so on the card: an administrator
    # approving this should know it is also an experiment.
    from app.agent.actions import REMINDER_CONTROL_FRACTION, _is_reminder_control

    control = sum(1 for a in appointment_ids if _is_reminder_control(a))
    tomorrow = (clock.local_now() + timedelta(days=1)).strftime("%a %d %b")

    actions.propose(
        db,
        audience_role=UserRole.HOSPITAL_ADMIN,
        audience_scope_id=hospital.id,
        action_name="send_appointment_reminders",
        args={"appointment_ids": appointment_ids},
        title=f"Remind {len(appointment_ids) - control} patients about {tomorrow}?",
        preview_text=(
            f"{len(appointment_ids)} appointments at {hospital.name} are the "
            f"most likely to be missed (up to "
            f"{int((task.payload.get('top_probability') or 0) * 100)}% "
            f"predicted).\n\n"
            f"{len(appointment_ids) - control} patients will be reminded. "
            f"{control} are held back as a control group "
            f"(1 in {REMINDER_CONTROL_FRACTION}), so the effect of reminding "
            "can be measured rather than assumed."
        ),
        evidence={
            "detector_id": "noshow_reminder_batch",
            "model": "no_show_logistic",
            "selection": f"top {BATCH_SIZE} by predicted no-show probability",
            "top_probability": task.payload.get("top_probability"),
            "hospital_id": hospital.id,
        },
        idempotency_key=f"noshow_batch:{hospital.id}:{clock.today().isoformat()}",
        origin="job",
        origin_ref=hospital.id,
        ttl_days=1,  # a reminder about tomorrow is worthless the day after
    )
    db.commit()


@register(
    "detect_noshow_batches",
    seconds=12 * 60 * 60,
    description="Propose reminder batches for appointments predicted to be missed",
)
def job() -> None:
    db = SessionLocal()
    try:
        detect(db)
    finally:
        db.close()
