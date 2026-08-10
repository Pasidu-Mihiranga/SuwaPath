"""Measuring the no-show model against what actually happened.

Two different questions, and the difference matters:

**Is the model any good?** Fit on the older part of the history, score the
newer part, compare against the outcomes. A clean time-split backtest, which
is the only honest split for a model whose features are built from a patient's
own past.

**Is the *deployed* model any good?** Read the `NoShowPrediction` rows the
system actually wrote, find the appointments that have since resolved, and
compare. This is the one that catches drift, because it grades the predictions
that were really made rather than predictions recomputed today with today's
data.

Until now `NoShowPrediction` had no reader at all — it was written on every
dashboard refresh and never looked at again. The second function below is what
makes it a real table.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.care import Appointment
from app.models.enums import AppointmentStatus
from app.models.platform import NoShowPrediction
from app.services import clock
from app.services.ml import metrics

logger = logging.getLogger(__name__)

# Outcomes only exist for appointments that resolved one way or the other.
RESOLVED = (AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW)

# Rows whose status was written by the automatic sweep are excluded from every
# label set here. They record that nobody closed an appointment, not that a
# patient failed to attend, and treating them as outcomes teaches the model to
# predict administrative neglect.
OBSERVED_ONLY = "auto_sweep"


def _label(appointment: Appointment) -> float:
    return 1.0 if str(appointment.status) == str(AppointmentStatus.NO_SHOW) else 0.0


def backtest_no_show(
    db: Session, *, hospital_id: str | None = None, holdout: float = 0.25
) -> dict:
    """Fit on the past, score the future, grade against the outcome.

    Every scored appointment is given a history bounded at *its own* scheduled
    time. Without that bound the patient's prior-no-show rate would include
    appointments that had not happened yet when the prediction would have been
    made, and the result would be an impressively wrong number.
    """
    from app.services.analytics import (
        FEATURE_NAMES,
        PatientHistory,
        _features,
        _patient_histories,
        _train_logistic,
    )

    conditions = [
        Appointment.status.in_([str(s) for s in RESOLVED]),
        Appointment.status_source != "auto_sweep",
    ]
    if hospital_id:
        conditions.append(Appointment.hospital_id == hospital_id)

    appointments = db.execute(
        select(Appointment).where(*conditions).order_by(Appointment.scheduled_start)
    ).scalars().all()

    if len(appointments) < 50:
        return {"status": "insufficient_data", "n": len(appointments)}

    train, test = metrics.time_split(
        appointments, key=lambda a: a.scheduled_start, holdout=holdout
    )
    if not test:
        return {"status": "insufficient_data", "n": len(appointments)}

    # Training history accumulates forward, exactly as it would have at the
    # time — no appointment contributes to its own features.
    running: dict[str, PatientHistory] = {}
    samples = []
    for appointment in train:
        history = running.get(appointment.patient_user_id, PatientHistory())
        samples.append((_features(appointment, history), _label(appointment)))
        running[appointment.patient_user_id] = PatientHistory(
            total=history.total + 1,
            no_shows=history.no_shows + int(_label(appointment)),
        )

    model = _train_logistic(samples)

    truth, scores = [], []
    for appointment in test:
        history = _patient_histories(
            db, [appointment.patient_user_id], before=appointment.scheduled_start
        ).get(appointment.patient_user_id, PatientHistory())
        scores.append(model.predict(_features(appointment, history)))
        truth.append(_label(appointment))

    evaluation = metrics.evaluate(truth, scores)
    return {
        "status": "ok",
        "split": {"train": len(train), "test": len(test)},
        "features": list(FEATURE_NAMES),
        **evaluation.to_dict(),
    }


def score_deployed_predictions(
    db: Session, *, hospital_id: str | None = None, days: int = 90
) -> dict:
    """Grade the predictions the system actually stored.

    The difference from a backtest is drift: this measures the model as it was
    when it ran, against outcomes that arrived afterwards. A backtest can look
    healthy while the deployed model quietly degrades.
    """
    since = clock.now() - timedelta(days=days)

    conditions = [
        Appointment.status.in_([str(s) for s in RESOLVED]),
        Appointment.scheduled_start >= since,
        Appointment.scheduled_start <= clock.now(),
        # Grading against our own sweep would compare the model to a label it
        # helped create. The first run of this function returned AUC=None
        # because every scored appointment had been swept, and both arms of
        # the reminder experiment showed a 100% no-show rate.
        Appointment.status_source != "auto_sweep",
    ]
    if hospital_id:
        conditions.append(Appointment.hospital_id == hospital_id)

    rows = db.execute(
        select(NoShowPrediction, Appointment)
        .join(Appointment, Appointment.id == NoShowPrediction.appointment_id)
        .where(*conditions)
    ).all()

    if not rows:
        return {
            "status": "no_resolved_predictions",
            "hint": (
                "Predictions are graded once their appointment has been "
                "completed or marked as missed."
            ),
        }

    truth = [_label(appointment) for _, appointment in rows]
    scores = [prediction.probability for prediction, _ in rows]

    evaluation = metrics.evaluate(truth, scores)
    reminded = [
        (t, (a.reminder_sent_count or 0) > 0) for t, (_, a) in zip(truth, rows)
    ]
    treated = [t for t, was_reminded in reminded if was_reminded]
    control = [t for t, was_reminded in reminded if not was_reminded]

    return {
        "status": "ok",
        "window_days": days,
        **evaluation.to_dict(),
        # The reminder experiment, reported rather than assumed. A fifth of
        # high-risk appointments are deliberately never reminded (see
        # `agent/actions.py`), which is what makes this comparison mean
        # anything at all.
        "reminder_effect": {
            "reminded": {
                "n": len(treated),
                "no_show_rate": round(sum(treated) / len(treated), 4) if treated else None,
            },
            "not_reminded": {
                "n": len(control),
                "no_show_rate": round(sum(control) / len(control), 4) if control else None,
            },
        },
    }
