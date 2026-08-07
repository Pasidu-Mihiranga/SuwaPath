"""Hospital operational intelligence: no-show prediction and demand forecasting.

Both models are fitted from the hospital's own historical appointments rather
than hard-coded, so the numbers on the dashboard are derived from the seeded
data and move when the data moves.

No-show model
    Logistic regression trained by gradient descent on completed/no-show
    history. Coefficients are exposed so staff see *why* an appointment is
    high risk, not just a number.

Demand forecast
    Additive decomposition: level + trend + day-of-week seasonality per
    specialty, with a residual-based prediction interval. Compared against real
    schedule capacity to raise capacity warnings.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.care import Appointment
from app.models.enums import AppointmentStatus, RiskBand, VisitType
from app.models.platform import Forecast, NoShowPrediction
from app.models.providers import Doctor, DoctorSchedule, Hospital, Specialty

logger = logging.getLogger(__name__)

MODEL_VERSION = "1.0.0"
HISTORY_WINDOW_DAYS = 240

# --------------------------------------------------------------------------
# Feature engineering
# --------------------------------------------------------------------------
FEATURE_NAMES = [
    "lead_time_days",
    "reschedule_count",
    "not_confirmed",
    "prior_no_show_rate",
    "distance_km",
    "is_monday",
    "is_early_morning",
    "is_teleconsultation",
    "reminders_sent",
]

# Plain-language labels for the dashboard's "contributing factors".
FEATURE_LABELS = {
    "lead_time_days": "Booked far in advance",
    "reschedule_count": "Rescheduled previously",
    "not_confirmed": "Appointment never confirmed",
    "prior_no_show_rate": "History of missed appointments",
    "distance_km": "Long travel distance",
    "is_monday": "Monday appointment",
    "is_early_morning": "Early morning slot",
    "is_teleconsultation": "Teleconsultation",
    "reminders_sent": "Reminders already sent",
}


@dataclass
class PatientHistory:
    total: int = 0
    no_shows: int = 0

    @property
    def rate(self) -> float:
        # Smoothed so a single miss on a short history is not treated as 100%.
        return (self.no_shows + 0.5) / (self.total + 3.0)


def _features(
    appointment: Appointment, history: PatientHistory
) -> list[float]:
    lead = appointment.lead_time_days
    return [
        min(lead if lead is not None else 5.0, 60.0) / 60.0,
        min(appointment.reschedule_count, 4) / 4.0,
        0.0 if appointment.confirmed_at else 1.0,
        history.rate,
        min(appointment.patient_distance_km or 8.0, 50.0) / 50.0,
        1.0 if appointment.scheduled_start.weekday() == 0 else 0.0,
        1.0 if appointment.scheduled_start.hour <= 8 else 0.0,
        1.0 if str(appointment.visit_type) == str(VisitType.TELECONSULTATION) else 0.0,
        min(appointment.reminder_sent_count, 3) / 3.0,
    ]


# --------------------------------------------------------------------------
# Logistic regression
# --------------------------------------------------------------------------
@dataclass
class LogisticModel:
    weights: list[float]
    bias: float
    trained_on: int = 0
    base_rate: float = 0.15

    def predict(self, features: list[float]) -> float:
        z = self.bias + sum(w * f for w, f in zip(self.weights, features))
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

    def contributions(self, features: list[float]) -> list[dict]:
        """Per-feature contribution to the log-odds, largest positive first."""
        items = [
            {
                "feature": name,
                "label": FEATURE_LABELS[name],
                "contribution": round(w * f, 4),
            }
            for name, w, f in zip(FEATURE_NAMES, self.weights, features)
        ]
        items.sort(key=lambda item: item["contribution"], reverse=True)
        return [item for item in items if item["contribution"] > 0.02][:4]


def _train_logistic(
    samples: list[tuple[list[float], int]],
    *,
    epochs: int = 260,
    learning_rate: float = 0.35,
    l2: float = 0.002,
) -> LogisticModel:
    """Batch gradient descent. Small dataset, so no need for anything heavier."""
    if not samples:
        # Sensible priors matching the seeded generative process.
        return LogisticModel(
            weights=[1.2, 0.9, 0.6, 1.6, 0.5, 0.2, 0.25, -0.4, -0.1],
            bias=-2.4,
        )

    n_features = len(samples[0][0])
    weights = [0.0] * n_features
    bias = 0.0
    n = len(samples)
    positive_rate = sum(label for _, label in samples) / n
    # Initialise the bias at the empirical log-odds for faster convergence.
    bias = math.log(max(positive_rate, 1e-3) / max(1 - positive_rate, 1e-3))

    for _ in range(epochs):
        grad_w = [0.0] * n_features
        grad_b = 0.0
        for features, label in samples:
            z = bias + sum(w * f for w, f in zip(weights, features))
            prediction = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            error = prediction - label
            for index, value in enumerate(features):
                grad_w[index] += error * value
            grad_b += error

        for index in range(n_features):
            weights[index] -= learning_rate * (grad_w[index] / n + l2 * weights[index])
        bias -= learning_rate * (grad_b / n)

    return LogisticModel(
        weights=weights, bias=bias, trained_on=n, base_rate=positive_rate
    )


def _band(probability: float, base_rate: float = 0.15) -> RiskBand:
    """Band a probability relative to the population's own no-show rate.

    Fixed thresholds break whenever the base rate shifts: at a 27% base rate a
    0.45 cut-off leaves the "high" bucket permanently empty, and at a 5% base
    rate almost everything looks low. Scaling to the base rate keeps the bands
    meaningful — "high" means clearly worse than typical for this hospital —
    while the floors stop tiny base rates producing alarmist labels.
    """
    high_cut = max(0.35, 2.0 * base_rate)
    medium_cut = max(0.18, 1.15 * base_rate)
    if probability >= high_cut:
        return RiskBand.HIGH
    if probability >= medium_cut:
        return RiskBand.MEDIUM
    return RiskBand.LOW


# --------------------------------------------------------------------------
# No-show prediction
# --------------------------------------------------------------------------
def train_no_show_model(db: Session, *, hospital_id: str | None = None) -> LogisticModel:
    """Fit the model on resolved historical appointments."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_WINDOW_DAYS)
    stmt = select(Appointment).where(
        Appointment.scheduled_start >= cutoff,
        Appointment.scheduled_start < datetime.now(timezone.utc),
        Appointment.status.in_(
            [str(AppointmentStatus.COMPLETED), str(AppointmentStatus.NO_SHOW)]
        ),
    )
    if hospital_id:
        stmt = stmt.where(Appointment.hospital_id == hospital_id)

    appointments = list(db.execute(stmt).scalars())
    if not appointments:
        return _train_logistic([])

    # Build per-patient history in chronological order so each sample only
    # sees prior outcomes — no leakage from the future.
    appointments.sort(key=lambda a: a.scheduled_start)
    histories: dict[str, PatientHistory] = defaultdict(PatientHistory)
    samples: list[tuple[list[float], int]] = []

    for appointment in appointments:
        history = histories[appointment.patient_user_id]
        # `==`, not `is`: SQLAlchemy hands back the raw column string, which is
        # equal to the StrEnum member but never identical to it.
        label = 1 if appointment.status == AppointmentStatus.NO_SHOW else 0
        samples.append((_features(appointment, history), label))
        history.total += 1
        history.no_shows += label

    return _train_logistic(samples)


def _patient_histories(db: Session, patient_ids: list[str]) -> dict[str, PatientHistory]:
    if not patient_ids:
        return {}
    rows = db.execute(
        select(
            Appointment.patient_user_id,
            func.count().label("total"),
            func.sum(
                case((Appointment.status == str(AppointmentStatus.NO_SHOW), 1), else_=0)
            ).label("no_shows"),
        )
        .where(
            Appointment.patient_user_id.in_(patient_ids),
            Appointment.status.in_(
                [str(AppointmentStatus.COMPLETED), str(AppointmentStatus.NO_SHOW)]
            ),
        )
        .group_by(Appointment.patient_user_id)
    ).all()
    return {
        row.patient_user_id: PatientHistory(total=row.total, no_shows=row.no_shows or 0)
        for row in rows
    }


def predict_no_shows(
    db: Session,
    *,
    hospital_id: str | None = None,
    days_ahead: int = 14,
    persist: bool = True,
) -> list[dict]:
    """Score upcoming appointments and optionally persist the predictions."""
    model = train_no_show_model(db, hospital_id=hospital_id)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days_ahead)

    stmt = (
        select(Appointment)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.specialty),
        )
        .where(
            Appointment.scheduled_start >= now,
            Appointment.scheduled_start <= horizon,
            Appointment.status.in_(
                [str(AppointmentStatus.PENDING), str(AppointmentStatus.CONFIRMED)]
            ),
        )
    )
    if hospital_id:
        stmt = stmt.where(Appointment.hospital_id == hospital_id)

    appointments = list(db.execute(stmt).scalars().unique())
    histories = _patient_histories(db, [a.patient_user_id for a in appointments])

    results: list[dict] = []
    for appointment in appointments:
        history = histories.get(appointment.patient_user_id, PatientHistory())
        features = _features(appointment, history)
        probability = model.predict(features)
        band = _band(probability, model.base_rate)

        results.append(
            {
                "appointment_id": appointment.id,
                "patient_name": appointment.patient.full_name if appointment.patient else None,
                "patient_user_id": appointment.patient_user_id,
                "doctor_name": (
                    appointment.doctor.user.full_name
                    if appointment.doctor and appointment.doctor.user
                    else None
                ),
                "specialty_code": (
                    appointment.doctor.specialty.code
                    if appointment.doctor and appointment.doctor.specialty
                    else None
                ),
                "scheduled_start": appointment.scheduled_start.isoformat(),
                "probability": round(probability, 4),
                "risk_band": str(band),
                "contributing_factors": model.contributions(features),
            }
        )

    if persist:
        _persist_predictions(db, results, model)

    results.sort(key=lambda r: r["probability"], reverse=True)
    return results


def _persist_predictions(db: Session, results: list[dict], model: LogisticModel) -> None:
    if not results:
        return
    appointment_ids = [r["appointment_id"] for r in results]
    existing = {
        prediction.appointment_id: prediction
        for prediction in db.execute(
            select(NoShowPrediction).where(
                NoShowPrediction.appointment_id.in_(appointment_ids)
            )
        ).scalars()
    }
    appointments = {
        a.id: a
        for a in db.execute(
            select(Appointment).where(Appointment.id.in_(appointment_ids))
        ).scalars()
    }

    for result in results:
        appointment = appointments.get(result["appointment_id"])
        if appointment is None:
            continue
        prediction = existing.get(result["appointment_id"])
        if prediction is None:
            prediction = NoShowPrediction(appointment_id=result["appointment_id"])
            db.add(prediction)
        prediction.hospital_id = appointment.hospital_id
        prediction.probability = result["probability"]
        prediction.risk_band = RiskBand(result["risk_band"])
        prediction.contributing_factors = result["contributing_factors"]
        prediction.model_version = MODEL_VERSION
        prediction.predicted_for_date = appointment.scheduled_start.date()

    db.commit()


def no_show_summary(db: Session, *, hospital_id: str | None = None) -> dict:
    """Aggregate view for the hospital dashboard."""
    predictions = predict_no_shows(db, hospital_id=hospital_id, persist=False)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()

    by_band = {"high": 0, "medium": 0, "low": 0}
    for prediction in predictions:
        by_band[prediction["risk_band"]] += 1

    tomorrow_high = [
        p for p in predictions
        if p["risk_band"] == "high"
        and datetime.fromisoformat(p["scheduled_start"]).date() == tomorrow
    ]

    # Historical realised rate, for comparison against predicted risk.
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    stmt = select(Appointment.status).where(
        Appointment.scheduled_start >= cutoff,
        Appointment.scheduled_start < datetime.now(timezone.utc),
        Appointment.status.in_(
            [str(AppointmentStatus.COMPLETED), str(AppointmentStatus.NO_SHOW)]
        ),
    )
    if hospital_id:
        stmt = stmt.where(Appointment.hospital_id == hospital_id)
    statuses = list(db.execute(stmt).scalars())
    realised = (
        sum(1 for s in statuses if s == str(AppointmentStatus.NO_SHOW)) / len(statuses)
        if statuses
        else 0.0
    )

    return {
        "total_upcoming": len(predictions),
        "by_risk_band": by_band,
        "tomorrow_high_risk_count": len(tomorrow_high),
        "tomorrow_high_risk": tomorrow_high[:20],
        "historical_no_show_rate": round(realised, 4),
        "top_risk": predictions[:15],
        "model_version": MODEL_VERSION,
    }


# --------------------------------------------------------------------------
# Specialty demand forecasting
# --------------------------------------------------------------------------
@dataclass
class SpecialtySeries:
    specialty_code: str
    daily_counts: dict[date, int] = field(default_factory=dict)


def _daily_demand(
    db: Session, *, hospital_id: str | None, days: int
) -> dict[str, dict[date, int]]:
    """Historical appointments per specialty per day."""
    start = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(
            Specialty.code,
            func.date(Appointment.scheduled_start).label("day"),
            func.count().label("count"),
        )
        # Explicit FROM: the join predicates reference Appointment, so it must
        # anchor the query rather than being inferred from the select list.
        .select_from(Appointment)
        .join(Doctor, Appointment.doctor_id == Doctor.id)
        .join(Specialty, Doctor.specialty_id == Specialty.id)
        .where(
            Appointment.scheduled_start >= start,
            Appointment.scheduled_start < datetime.now(timezone.utc),
            Appointment.status != str(AppointmentStatus.CANCELLED),
        )
        .group_by(Specialty.code, "day")
    )
    if hospital_id:
        stmt = stmt.where(Appointment.hospital_id == hospital_id)

    series: dict[str, dict[date, int]] = defaultdict(dict)
    for code, day, count in db.execute(stmt).all():
        parsed = day if isinstance(day, date) else datetime.fromisoformat(str(day)).date()
        series[code][parsed] = count
    return series


def _decompose(counts: dict[date, int], history_days: int) -> tuple[float, float, dict[int, float], float]:
    """Return (level, per-day trend, weekday factors, residual std)."""
    if not counts:
        return 0.0, 0.0, {d: 1.0 for d in range(7)}, 0.0

    end = max(counts)
    start = end - timedelta(days=history_days)
    dense = [
        (day, counts.get(day, 0))
        for day in (start + timedelta(days=i) for i in range(history_days + 1))
    ]
    values = [v for _, v in dense]
    n = len(values)
    level = sum(values) / n if n else 0.0

    # Ordinary least squares slope over the day index.
    mean_x = (n - 1) / 2
    denominator = sum((i - mean_x) ** 2 for i in range(n)) or 1.0
    slope = sum((i - mean_x) * (v - level) for i, v in enumerate(values)) / denominator

    # Multiplicative weekday factors around the detrended mean.
    weekday_totals: dict[int, list[float]] = defaultdict(list)
    for index, (day, value) in enumerate(dense):
        detrended = value - slope * (index - mean_x)
        weekday_totals[day.weekday()].append(detrended)

    factors: dict[int, float] = {}
    for weekday in range(7):
        bucket = weekday_totals.get(weekday) or [level]
        mean = sum(bucket) / len(bucket)
        factors[weekday] = (mean / level) if level > 0 else 1.0

    residuals = [
        value - max(0.0, (level + slope * (index - mean_x)) * factors[day.weekday()])
        for index, (day, value) in enumerate(dense)
    ]
    residual_std = (
        math.sqrt(sum(r * r for r in residuals) / len(residuals)) if residuals else 0.0
    )
    return level, slope, factors, residual_std


def _capacity_by_specialty(
    db: Session, *, hospital_id: str | None
) -> dict[str, dict[int, int]]:
    """Bookable slots per specialty per weekday, from published schedules."""
    stmt = (
        select(
            Specialty.code,
            DoctorSchedule.day_of_week,
            DoctorSchedule.start_time,
            DoctorSchedule.end_time,
            DoctorSchedule.slot_duration_minutes,
            DoctorSchedule.max_patients,
        )
        .join(Doctor, DoctorSchedule.doctor_id == Doctor.id)
        .join(Specialty, Doctor.specialty_id == Specialty.id)
        .where(DoctorSchedule.is_active.is_(True), Doctor.is_active.is_(True))
    )
    if hospital_id:
        stmt = stmt.where(DoctorSchedule.hospital_id == hospital_id)

    capacity: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for code, weekday, start, end, slot_minutes, max_patients in db.execute(stmt).all():
        minutes = (
            datetime.combine(date.today(), end) - datetime.combine(date.today(), start)
        ).total_seconds() / 60
        slots = int(minutes // max(slot_minutes, 1))
        capacity[code][weekday] += min(slots, max_patients)
    return capacity


def forecast_demand(
    db: Session,
    *,
    hospital_id: str | None = None,
    horizon_days: int = 7,
    history_days: int = 120,
    persist: bool = True,
) -> list[dict]:
    """Forecast the next `horizon_days` of demand per specialty vs capacity."""
    series = _daily_demand(db, hospital_id=hospital_id, days=history_days)
    capacity = _capacity_by_specialty(db, hospital_id=hospital_id)
    today = date.today()

    # Already-booked future appointments, so the dashboard can show
    # booked-vs-predicted rather than only a prediction.
    booked_stmt = (
        select(
            Specialty.code,
            func.date(Appointment.scheduled_start).label("day"),
            func.count().label("count"),
        )
        # Explicit FROM: the join predicates reference Appointment, so it must
        # anchor the query rather than being inferred from the select list.
        .select_from(Appointment)
        .join(Doctor, Appointment.doctor_id == Doctor.id)
        .join(Specialty, Doctor.specialty_id == Specialty.id)
        .where(
            Appointment.scheduled_start >= datetime.now(timezone.utc),
            Appointment.status.in_(
                [str(AppointmentStatus.PENDING), str(AppointmentStatus.CONFIRMED)]
            ),
        )
        .group_by(Specialty.code, "day")
    )
    if hospital_id:
        booked_stmt = booked_stmt.where(Appointment.hospital_id == hospital_id)
    booked: dict[tuple[str, date], int] = {}
    for code, day, count in db.execute(booked_stmt).all():
        parsed = day if isinstance(day, date) else datetime.fromisoformat(str(day)).date()
        booked[(code, parsed)] = count

    out: list[dict] = []
    generated_at = datetime.now(timezone.utc)

    for specialty_code, counts in series.items():
        level, slope, factors, residual_std = _decompose(counts, history_days)
        if level <= 0:
            continue

        for offset in range(1, horizon_days + 1):
            target = today + timedelta(days=offset)
            weekday = target.weekday()
            # Project the trend forward from the centre of the history window.
            trend_component = level + slope * (history_days / 2 + offset)
            predicted = max(0.0, trend_component * factors.get(weekday, 1.0))

            # 80% interval from residual spread, widening with horizon.
            interval = 1.28 * residual_std * math.sqrt(1 + offset / horizon_days)
            day_capacity = capacity.get(specialty_code, {}).get(weekday, 0)
            already = booked.get((specialty_code, target), 0)

            utilisation = (predicted / day_capacity * 100) if day_capacity else 0.0
            record = {
                "specialty_code": specialty_code,
                "forecast_date": target.isoformat(),
                "day_label": target.strftime("%a %d %b"),
                "predicted_demand": round(predicted, 1),
                "lower_bound": round(max(0.0, predicted - interval), 1),
                "upper_bound": round(predicted + interval, 1),
                "available_capacity": day_capacity,
                "already_booked": already,
                "capacity_warning": bool(day_capacity and predicted > day_capacity),
                "utilisation_percent": round(utilisation, 1),
            }
            out.append(record)

            if persist and hospital_id:
                _upsert_forecast(db, hospital_id, record, generated_at)

    if persist and hospital_id:
        db.commit()

    out.sort(key=lambda r: (r["forecast_date"], -r["predicted_demand"]))
    return out


def _upsert_forecast(
    db: Session, hospital_id: str, record: dict, generated_at: datetime
) -> None:
    forecast_date = date.fromisoformat(record["forecast_date"])
    existing = db.execute(
        select(Forecast).where(
            and_(
                Forecast.hospital_id == hospital_id,
                Forecast.specialty_code == record["specialty_code"],
                Forecast.forecast_date == forecast_date,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = Forecast(
            hospital_id=hospital_id,
            specialty_code=record["specialty_code"],
            forecast_date=forecast_date,
        )
        db.add(existing)

    existing.predicted_demand = record["predicted_demand"]
    existing.lower_bound = record["lower_bound"]
    existing.upper_bound = record["upper_bound"]
    existing.available_capacity = record["available_capacity"]
    existing.already_booked = record["already_booked"]
    existing.capacity_warning = record["capacity_warning"]
    existing.utilisation_percent = record["utilisation_percent"]
    existing.model_version = MODEL_VERSION
    existing.generated_at = generated_at


def specialty_demand_summary(
    db: Session, *, hospital_id: str | None = None, horizon_days: int = 7
) -> dict:
    """Per-specialty totals over the horizon, with capacity warnings."""
    rows = forecast_demand(
        db, hospital_id=hospital_id, horizon_days=horizon_days, persist=bool(hospital_id)
    )

    by_specialty: dict[str, dict] = {}
    for row in rows:
        entry = by_specialty.setdefault(
            row["specialty_code"],
            {
                "specialty_code": row["specialty_code"],
                "predicted_total": 0.0,
                "capacity_total": 0,
                "booked_total": 0,
                "warning_days": 0,
                "daily": [],
            },
        )
        entry["predicted_total"] += row["predicted_demand"]
        entry["capacity_total"] += row["available_capacity"]
        entry["booked_total"] += row["already_booked"]
        entry["warning_days"] += 1 if row["capacity_warning"] else 0
        entry["daily"].append(row)

    summaries = []
    for entry in by_specialty.values():
        entry["predicted_total"] = round(entry["predicted_total"])
        entry["capacity_warning"] = (
            entry["capacity_total"] > 0
            and entry["predicted_total"] > entry["capacity_total"]
        )
        entry["utilisation_percent"] = (
            round(entry["predicted_total"] / entry["capacity_total"] * 100, 1)
            if entry["capacity_total"]
            else 0.0
        )
        summaries.append(entry)

    summaries.sort(key=lambda e: e["predicted_total"], reverse=True)
    return {
        "horizon_days": horizon_days,
        "specialties": summaries,
        "warnings": [s for s in summaries if s["capacity_warning"]],
        "model_version": MODEL_VERSION,
    }
