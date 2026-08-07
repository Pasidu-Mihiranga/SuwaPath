"""Hospital administrator dashboard: operational KPIs, forecasting, capacity.

Administrators see operational and administrative information only — never
patients' clinical conversations (internal rule 9).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.security import get_current_user, require_admin
from app.models.care import Appointment, CareEnrollment, Consultation, Referral
from app.models.clinical import StructuredIntake
from app.models.enums import AppointmentStatus, ReferralStatus, UserRole
from app.models.identity import User
from app.models.platform import HospitalAlert
from app.models.providers import Doctor, DoctorSchedule, FacilityCapability, Hospital, Specialty
from app.services.analytics import (
    forecast_demand,
    no_show_summary,
    predict_no_shows,
    specialty_demand_summary,
)

router = APIRouter(prefix="/hospital", tags=["hospital-admin"])


def _scope(current_user: User, hospital_id: str | None) -> str | None:
    """Resolve which hospital the caller may query."""
    if str(current_user.role) == str(UserRole.SYSTEM_ADMIN):
        return hospital_id  # None means system-wide
    if not current_user.hospital_id:
        raise HTTPException(status_code=403, detail="Account not linked to a hospital.")
    if hospital_id and hospital_id != current_user.hospital_id:
        raise HTTPException(
            status_code=403, detail="You can only view your own facility."
        )
    return current_user.hospital_id


@router.get("/dashboard")
def dashboard(
    hospital_id: str | None = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Headline KPIs with month-on-month comparison."""
    scope = _scope(current_user, hospital_id)
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)

    def appointment_stats(start: datetime, end: datetime) -> dict:
        stmt = select(
            func.count().label("total"),
            func.sum(case((Appointment.status == str(AppointmentStatus.NO_SHOW), 1), else_=0)).label("no_shows"),
            func.sum(case((Appointment.status == str(AppointmentStatus.COMPLETED), 1), else_=0)).label("completed"),
        ).where(Appointment.scheduled_start >= start, Appointment.scheduled_start < end)
        if scope:
            stmt = stmt.where(Appointment.hospital_id == scope)
        row = db.execute(stmt).one()
        total = row.total or 0
        resolved = (row.no_shows or 0) + (row.completed or 0)
        return {
            "total": total,
            "no_shows": row.no_shows or 0,
            "completed": row.completed or 0,
            "no_show_rate": round((row.no_shows or 0) / resolved * 100, 1) if resolved else 0.0,
        }

    this_month = appointment_stats(month_start, now)
    last_month = appointment_stats(prev_month_start, month_start)

    # Intakes = structured symptom intakes leading to this facility.
    intake_stmt = select(func.count()).select_from(StructuredIntake).where(
        StructuredIntake.created_at >= month_start
    )
    intakes = db.execute(intake_stmt).scalar() or 0

    referral_stmt = select(
        func.count().label("total"),
        func.sum(case((Referral.status == str(ReferralStatus.COMPLETED), 1), else_=0)).label("completed"),
    )
    if scope:
        referral_stmt = referral_stmt.where(Referral.to_hospital_id == scope)
    referral_row = db.execute(referral_stmt).one()

    # Average intake-to-consultation time, in days.
    wait_stmt = select(
        func.avg(
            func.extract("epoch", Appointment.scheduled_start - Appointment.booked_at)
            / 86400.0
        )
    ).where(
        Appointment.booked_at.isnot(None),
        Appointment.scheduled_start >= month_start,
    )
    if scope:
        wait_stmt = wait_stmt.where(Appointment.hospital_id == scope)
    avg_wait = db.execute(wait_stmt).scalar()

    demand = specialty_demand_summary(db, hospital_id=scope, horizon_days=7)
    top_specialty = demand["specialties"][0] if demand["specialties"] else None

    hospital = db.get(Hospital, scope) if scope else None

    return {
        "hospital": (
            {"id": hospital.id, "name": hospital.name, "city": hospital.city}
            if hospital
            else {"id": None, "name": "All facilities", "city": None}
        ),
        "period": {"from": month_start.date(), "to": now.date()},
        "kpis": {
            "total_intakes": intakes,
            "appointments_booked": this_month["total"],
            "appointments_booked_prev": last_month["total"],
            "no_show_rate": this_month["no_show_rate"],
            "no_show_rate_prev": last_month["no_show_rate"],
            "referrals_completed": referral_row.completed or 0,
            "referrals_total": referral_row.total or 0,
            "avg_intake_to_consult_days": round(avg_wait, 1) if avg_wait else None,
            "top_specialty": (
                {
                    "code": top_specialty["specialty_code"],
                    "predicted_demand": top_specialty["predicted_total"],
                    "capacity_warning": top_specialty["capacity_warning"],
                }
                if top_specialty
                else None
            ),
        },
        "capacity_warnings": [
            {
                "specialty_code": s["specialty_code"],
                "predicted_total": s["predicted_total"],
                "capacity_total": s["capacity_total"],
                "utilisation_percent": s["utilisation_percent"],
            }
            for s in demand["warnings"]
        ],
    }


@router.get("/forecast")
def demand_forecast(
    hospital_id: str | None = None,
    horizon_days: int = Query(default=7, ge=1, le=30),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    scope = _scope(current_user, hospital_id)
    return {
        "daily": forecast_demand(
            db, hospital_id=scope, horizon_days=horizon_days, persist=bool(scope)
        ),
        "by_specialty": specialty_demand_summary(
            db, hospital_id=scope, horizon_days=horizon_days
        ),
    }


@router.get("/no-show")
def no_show(
    hospital_id: str | None = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    scope = _scope(current_user, hospital_id)
    return no_show_summary(db, hospital_id=scope)


@router.post("/no-show/refresh")
def refresh_no_show(
    hospital_id: str | None = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    scope = _scope(current_user, hospital_id)
    predictions = predict_no_shows(db, hospital_id=scope, persist=True)
    return {"predicted": len(predictions)}


@router.get("/capacity")
def capacity(
    hospital_id: str | None = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Doctor availability and utilisation per specialty for the coming week."""
    scope = _scope(current_user, hospital_id)
    today = date.today()
    week_end = today + timedelta(days=7)

    stmt = (
        select(
            Specialty.code,
            Specialty.name,
            func.count(func.distinct(Doctor.id)).label("doctor_count"),
        )
        .join(Doctor, Doctor.specialty_id == Specialty.id)
        .where(Doctor.is_active.is_(True))
        .group_by(Specialty.code, Specialty.name)
    )
    if scope:
        stmt = stmt.where(Doctor.hospital_id == scope)
    specialty_rows = db.execute(stmt).all()

    booked_stmt = (
        select(Specialty.code, func.count().label("booked"))
        # select_from is required: without it SQLAlchemy infers FROM from the
        # first selected entity (Specialty) and never joins in appointments.
        .select_from(Appointment)
        .join(Doctor, Appointment.doctor_id == Doctor.id)
        .join(Specialty, Doctor.specialty_id == Specialty.id)
        .where(
            Appointment.scheduled_start >= datetime.combine(today, time.min, tzinfo=timezone.utc),
            Appointment.scheduled_start <= datetime.combine(week_end, time.max, tzinfo=timezone.utc),
            Appointment.status.notin_([str(AppointmentStatus.CANCELLED)]),
        )
        .group_by(Specialty.code)
    )
    if scope:
        booked_stmt = booked_stmt.where(Appointment.hospital_id == scope)
    booked = {code: count for code, count in db.execute(booked_stmt).all()}

    demand = specialty_demand_summary(db, hospital_id=scope, horizon_days=7)
    capacity_by_code = {s["specialty_code"]: s for s in demand["specialties"]}

    out = []
    for code, name, doctor_count in specialty_rows:
        entry = capacity_by_code.get(code, {})
        weekly_capacity = entry.get("capacity_total", 0)
        weekly_booked = booked.get(code, 0)
        out.append(
            {
                "specialty_code": code,
                "specialty_name": name,
                "doctor_count": doctor_count,
                "weekly_capacity": weekly_capacity,
                "booked": weekly_booked,
                "predicted_demand": entry.get("predicted_total", 0),
                "utilisation_percent": (
                    round(weekly_booked / weekly_capacity * 100, 1)
                    if weekly_capacity
                    else 0.0
                ),
                "capacity_warning": entry.get("capacity_warning", False),
            }
        )
    out.sort(key=lambda e: e["utilisation_percent"], reverse=True)

    hospital = db.get(Hospital, scope) if scope else None
    return {
        "facility": (
            {
                "id": hospital.id,
                "name": hospital.name,
                "bed_count": hospital.bed_count,
                "icu_bed_count": hospital.icu_bed_count,
                "consultation_rooms": hospital.consultation_rooms,
            }
            if hospital
            else None
        ),
        "specialties": out,
    }


@router.get("/doctors")
def hospital_doctors(
    hospital_id: str | None = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Roster with this week's booked load per doctor."""
    scope = _scope(current_user, hospital_id)
    today = date.today()

    stmt = (
        select(Doctor)
        .options(
            selectinload(Doctor.user),
            selectinload(Doctor.specialty),
            selectinload(Doctor.schedules),
        )
        .where(Doctor.is_active.is_(True))
    )
    if scope:
        stmt = stmt.where(Doctor.hospital_id == scope)
    doctors = db.execute(stmt).scalars().unique().all()

    week_start = datetime.combine(today, time.min, tzinfo=timezone.utc)
    week_end = datetime.combine(today + timedelta(days=7), time.max, tzinfo=timezone.utc)

    load_stmt = (
        select(Appointment.doctor_id, func.count().label("booked"))
        .where(
            Appointment.scheduled_start >= week_start,
            Appointment.scheduled_start <= week_end,
            Appointment.status.notin_([str(AppointmentStatus.CANCELLED)]),
        )
        .group_by(Appointment.doctor_id)
    )
    load = {doctor_id: count for doctor_id, count in db.execute(load_stmt).all()}

    out = []
    for doctor in doctors:
        weekly_slots = 0
        for schedule in doctor.schedules:
            if not schedule.is_active:
                continue
            minutes = (
                datetime.combine(today, schedule.end_time)
                - datetime.combine(today, schedule.start_time)
            ).total_seconds() / 60
            weekly_slots += min(
                int(minutes // max(schedule.slot_duration_minutes, 1)),
                schedule.max_patients,
            )
        booked = load.get(doctor.id, 0)
        out.append(
            {
                "doctor_id": doctor.id,
                "name": doctor.user.full_name if doctor.user else None,
                "specialty_name": doctor.specialty.name if doctor.specialty else None,
                "sub_specialty": doctor.sub_specialty,
                "years_experience": doctor.years_experience,
                "verification_status": str(doctor.verification_status),
                "accepts_new_patients": doctor.accepts_new_patients,
                "weekly_capacity": weekly_slots,
                "weekly_booked": booked,
                "availability_percent": (
                    round(max(0, weekly_slots - booked) / weekly_slots * 100)
                    if weekly_slots
                    else 0
                ),
            }
        )
    out.sort(key=lambda d: d["weekly_booked"], reverse=True)
    return out


@router.get("/alerts")
def hospital_alerts(
    hospital_id: str | None = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Live operational alerts, computed from current data."""
    scope = _scope(current_user, hospital_id)
    alerts: list[dict] = []

    demand = specialty_demand_summary(db, hospital_id=scope, horizon_days=7)
    for warning in demand["warnings"][:5]:
        alerts.append(
            {
                "alert_type": "capacity_constraint",
                "severity": "attention",
                "title": "Predicted demand exceeds capacity",
                "detail": (
                    f"{warning['specialty_code'].replace('_', ' ').title()}: "
                    f"predicted {warning['predicted_total']} vs capacity "
                    f"{warning['capacity_total']} over the next 7 days "
                    f"({warning['utilisation_percent']}%)."
                ),
            }
        )

    no_show_data = no_show_summary(db, hospital_id=scope)
    if no_show_data["tomorrow_high_risk_count"]:
        alerts.append(
            {
                "alert_type": "no_show_risk",
                "severity": "attention",
                "title": "High no-show risk tomorrow",
                "detail": (
                    f"{no_show_data['tomorrow_high_risk_count']} appointments "
                    f"tomorrow are high risk. Consider reminder calls or "
                    f"releasing capacity."
                ),
            }
        )

    overdue_stmt = select(func.count()).select_from(Referral).where(
        Referral.status == str(ReferralStatus.PENDING),
        Referral.due_date < date.today(),
    )
    if scope:
        overdue_stmt = overdue_stmt.where(Referral.to_hospital_id == scope)
    overdue = db.execute(overdue_stmt).scalar() or 0
    if overdue:
        alerts.append(
            {
                "alert_type": "overdue_referrals",
                "severity": "attention",
                "title": "Overdue referrals",
                "detail": f"{overdue} referrals are past their due date.",
            }
        )

    stored = db.execute(
        select(HospitalAlert)
        .where(
            HospitalAlert.is_resolved.is_(False),
            *( [HospitalAlert.hospital_id == scope] if scope else [] ),
        )
        .order_by(HospitalAlert.created_at.desc())
        .limit(10)
    ).scalars().all()
    for alert in stored:
        alerts.append(
            {
                "alert_type": alert.alert_type,
                "severity": str(alert.severity),
                "title": alert.title,
                "detail": alert.detail,
            }
        )
    return alerts


@router.get("/programmes")
def programme_enrolment(
    hospital_id: str | None = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Care-programme enrolment counts (aggregate only — no clinical detail)."""
    scope = _scope(current_user, hospital_id)
    from app.models.care import CareProgramme

    stmt = (
        select(CareProgramme.name, CareProgramme.code, func.count().label("total"))
        .join(CareEnrollment, CareEnrollment.programme_id == CareProgramme.id)
        .where(CareEnrollment.status == "active")
        .group_by(CareProgramme.name, CareProgramme.code)
    )
    if scope:
        stmt = stmt.where(CareEnrollment.hospital_id == scope)

    rows = db.execute(stmt).all()
    total = sum(r.total for r in rows)
    return {
        "total": total,
        "programmes": [
            {
                "code": r.code,
                "name": r.name,
                "enrolled": r.total,
                "share_percent": round(r.total / total * 100, 1) if total else 0.0,
            }
            for r in rows
        ],
    }


@router.get("/referrals")
def referral_pipeline(
    hospital_id: str | None = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    scope = _scope(current_user, hospital_id)
    stmt = select(Referral.status, func.count().label("total")).group_by(Referral.status)
    if scope:
        stmt = stmt.where(Referral.to_hospital_id == scope)

    rows = db.execute(stmt).all()
    total = sum(r.total for r in rows)
    by_status = {str(r.status): r.total for r in rows}
    completed = by_status.get(str(ReferralStatus.COMPLETED), 0)
    return {
        "total": total,
        "by_status": by_status,
        "completion_rate": round(completed / total * 100, 1) if total else 0.0,
    }


@router.get("/appointments/demand-history")
def demand_history(
    hospital_id: str | None = None,
    days: int = Query(default=30, ge=7, le=180),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Daily booked counts, for the forecast chart's historical series."""
    scope = _scope(current_user, hospital_id)
    start = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            func.date(Appointment.scheduled_start).label("day"),
            func.count().label("booked"),
        )
        .where(
            Appointment.scheduled_start >= start,
            Appointment.scheduled_start < datetime.now(timezone.utc),
            Appointment.status != str(AppointmentStatus.CANCELLED),
        )
        .group_by("day")
        .order_by("day")
    )
    if scope:
        stmt = stmt.where(Appointment.hospital_id == scope)

    return [
        {"date": str(day), "booked": count} for day, count in db.execute(stmt).all()
    ]
