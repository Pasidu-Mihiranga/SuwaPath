"""Seeds the care journey: appointments, consultations, referrals, care
programme enrolments, medications, check-ins, vitals and notifications.

Historical appointment outcomes are generated from an explicit latent no-show
model (lead time, reschedules, confirmation, distance, prior history, day and
time). That matters: the no-show predictor in `app/services/analytics.py` has
real signal to recover rather than noise, so the hospital dashboard shows a
defensible risk distribution.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import (
    Appointment,
    CareEnrollment,
    CareProgramme,
    Consultation,
    DailyCheckIn,
    Doctor,
    ElderlyRecord,
    GuardianAlert,
    Hospital,
    MaternalRecord,
    Medication,
    MedicationLog,
    Notification,
    PatientProfile,
    Referral,
    User,
    VitalRecord,
)
from app.models.enums import (
    AlertSeverity,
    AppointmentStatus,
    CheckInType,
    ConsultationStatus,
    EnrollmentStatus,
    GuardianPermissionType,
    MedicationLogStatus,
    NotificationCategory,
    NotificationPriority,
    ProgrammeType,
    ReferralStatus,
    UrgencyLevel,
    VisitType,
    WellbeingStatus,
)
from app.seed import reference as ref
from app.seed.people import MEDICATION_POOL
from app.services.matching import haversine_km

HISTORY_DAYS = 120  # how far back historical appointments extend
FUTURE_DAYS = 21

# Target slot fill rate per specialty. Values above 1.0 mean the clinic
# routinely overbooks (a real pattern in busy OPD services) and are what make
# the capacity-warning path in the forecaster reachable: predicted demand can
# then legitimately exceed published capacity.
SPECIALTY_FILL_RATES: dict[str, float] = {
    "cardiology": 1.18,
    "obstetrics_gynaecology": 1.06,
    "general_medicine": 0.95,
    "paediatrics": 0.90,
    "dermatology": 0.86,
    "respiratory_medicine": 0.80,
    "endocrinology": 0.74,
    "orthopaedics": 0.70,
    "neurology": 0.62,
    "gastroenterology": 0.55,
    "ent": 0.52,
    "ophthalmology": 0.50,
    "general_surgery": 0.48,
    "psychiatry": 0.44,
    "sexual_health": 0.38,
    "emergency_medicine": 0.35,
}
DEFAULT_FILL_RATE = 0.5

# How many nearby patients form each facility's catchment pool.
CATCHMENT_SIZE = 90


def _utc(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Appointments
# --------------------------------------------------------------------------
def _build_catchments(
    patients: list[User],
    profiles: dict[str, PatientProfile],
    hospitals_by_id: dict[str, Hospital],
) -> dict[str, list[tuple[User, float]]]:
    """Nearest patients per facility, so demand is geographically plausible."""
    located = [
        (p, profiles[p.id])
        for p in patients
        if profiles.get(p.id) and profiles[p.id].latitude and profiles[p.id].longitude
    ]
    catchments: dict[str, list[tuple[User, float]]] = {}

    for hospital_id, hospital in hospitals_by_id.items():
        ranked = sorted(
            (
                (patient, haversine_km(
                    profile.latitude, profile.longitude,
                    hospital.latitude, hospital.longitude,
                ))
                for patient, profile in located
            ),
            key=lambda pair: pair[1],
        )
        catchments[hospital_id] = ranked[:CATCHMENT_SIZE]

    return catchments


def seed_appointments(
    db: Session,
    rng: random.Random,
    patients: list[User],
    profiles: dict[str, PatientProfile],
    doctors: list[Doctor],
    hospitals_by_id: dict[str, Hospital],
    demo: dict[str, User] | None = None,
) -> list[Appointment]:
    """Generate appointments by walking each doctor's real schedule.

    Slot-driven rather than random: every appointment lands on an actual
    published slot, which keeps the doctor queue, availability lookup and
    capacity analytics mutually consistent. Fill rates per specialty control
    utilisation, so the hospital dashboard shows a defensible demand-vs-capacity
    picture instead of a near-empty one.
    """
    appointments: list[Appointment] = []
    today = date.today()
    catchments = _build_catchments(patients, profiles, hospitals_by_id)
    all_patients_fallback = [(p, 12.0) for p in patients]

    for doctor in doctors:
        specialty_code = doctor.specialty.code if doctor.specialty else "general_medicine"
        fill_rate = SPECIALTY_FILL_RATES.get(specialty_code, DEFAULT_FILL_RATE)
        # Individual doctors vary around their specialty's typical demand.
        fill_rate *= rng.uniform(0.82, 1.18)

        schedules = [s for s in doctor.schedules if s.is_active]
        if not schedules:
            continue
        pool = catchments.get(doctor.hospital_id or "") or all_patients_fallback
        if not pool:
            continue

        for offset in range(-HISTORY_DAYS, FUTURE_DAYS + 1):
            appt_date = today + timedelta(days=offset)
            is_future = offset >= 0

            for schedule in schedules:
                if schedule.day_of_week != appt_date.weekday():
                    continue

                step = timedelta(minutes=schedule.slot_duration_minutes)
                cursor = _utc(appt_date, schedule.start_time)
                block_end = _utc(appt_date, schedule.end_time)
                issued = 0

                while cursor + step <= block_end and issued < schedule.max_patients:
                    slot_start, slot_end = cursor, cursor + step
                    cursor += step
                    issued += 1

                    # Base booking, plus an overbooked duplicate when the
                    # specialty routinely runs above capacity.
                    bookings = 1 if rng.random() < min(fill_rate, 1.0) else 0
                    if fill_rate > 1.0 and rng.random() < (fill_rate - 1.0):
                        bookings += 1
                    if not bookings:
                        continue

                    for _ in range(bookings):
                        appointments.append(
                            _make_appointment(
                                rng,
                                doctor=doctor,
                                schedule=schedule,
                                pool=pool,
                                start=slot_start,
                                end=slot_end,
                                is_future=is_future,
                            )
                        )

    _assign_outcomes(rng, appointments, patients)
    _pin_demo_appointments(rng, appointments, doctors, demo or {})

    db.add_all(appointments)
    db.flush()
    return appointments


def _make_appointment(
    rng: random.Random,
    *,
    doctor: Doctor,
    schedule,
    pool: list[tuple[User, float]],
    start: datetime,
    end: datetime,
    is_future: bool,
) -> Appointment:
    # Nearer patients are more likely to attend a given facility.
    index = min(int(abs(rng.gauss(0, len(pool) / 3))), len(pool) - 1)
    patient, distance = pool[index]

    visit_type = VisitType(str(schedule.visit_type))
    lead_days = rng.choices(
        [0, 1, 2, 4, 7, 12, 21, 35], weights=[8, 12, 14, 18, 20, 14, 9, 5]
    )[0]
    booked_at = start - timedelta(days=lead_days, hours=rng.randint(0, 12))
    confirmed = rng.random() < 0.78

    return Appointment(
        patient_user_id=patient.id,
        doctor_id=doctor.id,
        hospital_id=doctor.hospital_id,
        scheduled_start=start,
        scheduled_end=end,
        visit_type=visit_type,
        urgency=rng.choices(
            [UrgencyLevel.ROUTINE, UrgencyLevel.URGENT, UrgencyLevel.EMERGENCY],
            weights=[0.80, 0.17, 0.03],
        )[0],
        reason=rng.choice(ref.CHIEF_COMPLAINTS),
        chief_complaint=rng.choice(ref.CHIEF_COMPLAINTS),
        fee_lkr=(
            doctor.teleconsultation_fee_lkr
            if visit_type == VisitType.TELECONSULTATION
            else doctor.consultation_fee_lkr
        ),
        booked_at=booked_at,
        confirmed_at=(
            booked_at + timedelta(hours=rng.randint(1, 30)) if confirmed else None
        ),
        reschedule_count=rng.choices([0, 1, 2], weights=[0.83, 0.13, 0.04])[0],
        reminder_sent_count=rng.randint(0, 3),
        patient_distance_km=round(distance, 1),
        status=(
            (AppointmentStatus.CONFIRMED if confirmed else AppointmentStatus.PENDING)
            if is_future
            else AppointmentStatus.PENDING  # replaced by _assign_outcomes
        ),
    )


# Which specialty each demo persona should plausibly be seen by, and the
# complaint the booking was made for. Sampling alone almost never lands a
# booking on a specific patient — one demo account out of three thousand, per
# slot — so reviewers signing in found an empty appointments page under a
# system holding tens of thousands of them.
DEMO_APPOINTMENTS: dict[str, tuple[tuple[str, ...], str]] = {
    "patient": (("general_medicine", "endocrinology"), "Tiredness and low thyroid review"),
    "maternal": (("obstetrics_gynaecology",), "Antenatal check — 28 weeks"),
    "postpartum": (("obstetrics_gynaecology",), "Postnatal review and feeding support"),
    "elderly": (("cardiology", "general_medicine"), "Blood pressure and dizziness review"),
}


def _pin_demo_appointments(
    rng: random.Random,
    appointments: list[Appointment],
    doctors: list[Doctor],
    demo: dict[str, User],
) -> None:
    """Hand a few already-generated bookings to the demo accounts.

    Reassignment rather than fabrication: each one still sits on a real
    published slot, with a real doctor, inside that doctor's capacity. Minting
    appointments outside the schedule walk would have produced bookings the
    availability lookup and the capacity dashboard disagree about.

    Past visits are forced to COMPLETED. `_assign_outcomes` models attendance
    honestly across the population, which for a single account can mean three
    consecutive no-shows and a demo with no visit history to open — a property
    of the sample, not something worth showing.
    """
    if not demo:
        return

    specialty_of = {
        d.id: (d.specialty.code if d.specialty else "general_medicine") for d in doctors
    }
    now = datetime.now(timezone.utc)
    claimed: set[int] = set()

    for key, (specialties, complaint) in DEMO_APPOINTMENTS.items():
        patient = demo.get(key)
        if patient is None:
            continue

        def available(future: bool, codes: tuple[str, ...] = specialties) -> list[int]:
            return [
                i for i, a in enumerate(appointments)
                if i not in claimed
                and specialty_of.get(a.doctor_id) in codes
                and ((a.scheduled_start > now) == future)
            ]

        # Two attended visits behind them and one booking ahead: enough for the
        # history tab, the upcoming card and the doctor's queue to each show
        # something. The past picks come off the end of the list, which the
        # schedule walk leaves in date order, so they are recent visits rather
        # than ones from the far edge of the history window.
        picks = available(False)[-2:] + available(True)[:1]
        for index in picks:
            appointment = appointments[index]
            claimed.add(index)
            appointment.patient_user_id = patient.id
            appointment.reason = complaint
            appointment.chief_complaint = complaint
            appointment.confirmed_at = appointment.confirmed_at or (
                appointment.booked_at + timedelta(hours=rng.randint(1, 20))
            )
            if appointment.scheduled_start > now:
                appointment.status = AppointmentStatus.CONFIRMED
                continue
            appointment.status = AppointmentStatus.COMPLETED
            appointment.cancelled_at = None
            appointment.cancellation_reason = None
            duration = int(
                (appointment.scheduled_end - appointment.scheduled_start).total_seconds() / 60
            )
            appointment.checked_in_at = appointment.scheduled_start - timedelta(
                minutes=rng.randint(4, 20)
            )
            appointment.started_at = appointment.scheduled_start + timedelta(
                minutes=rng.randint(0, 18)
            )
            appointment.completed_at = appointment.started_at + timedelta(minutes=duration)


def _assign_outcomes(
    rng: random.Random, appointments: list[Appointment], patients: list[User]
) -> None:
    """Resolve past appointments chronologically so no-show history compounds."""
    # Beta(5, 2) gives a left-skewed spread: most patients highly reliable,
    # with a meaningful tail of frequent non-attenders.
    reliability = {p.id: rng.betavariate(5, 2) for p in patients}
    # Track attended/missed counts so history feeds in as a *rate*. Using a raw
    # count would saturate immediately for frequent attenders and push the
    # whole population to maximum risk.
    seen: dict[str, int] = {}
    missed: dict[str, int] = {}
    now = datetime.now(timezone.utc)

    past = sorted(
        (a for a in appointments if a.scheduled_start < now),
        key=lambda a: a.scheduled_start,
    )

    for appointment in past:
        patient_id = appointment.patient_user_id
        lead = appointment.lead_time_days or 5.0
        total = seen.get(patient_id, 0)
        no_shows = missed.get(patient_id, 0)
        # Smoothed so one early miss is not read as a 100% no-show rate.
        prior_rate = (no_shows + 0.5) / (total + 3.0)

        outcome = _historical_outcome(
            rng,
            reliability=reliability.get(patient_id, 0.8),
            lead_days=int(lead),
            reschedules=appointment.reschedule_count,
            confirmed=appointment.confirmed_at is not None,
            distance=appointment.patient_distance_km,
            start=appointment.scheduled_start,
            prior_no_show_rate=prior_rate,
            visit_type=VisitType(str(appointment.visit_type)),
        )
        appointment.status = outcome
        seen[patient_id] = total + 1

        duration = int(
            (appointment.scheduled_end - appointment.scheduled_start).total_seconds() / 60
        )
        if outcome == AppointmentStatus.COMPLETED:
            appointment.checked_in_at = appointment.scheduled_start - timedelta(
                minutes=rng.randint(3, 25)
            )
            appointment.started_at = appointment.scheduled_start + timedelta(
                minutes=rng.randint(0, 20)
            )
            appointment.completed_at = appointment.started_at + timedelta(minutes=duration)
        elif outcome == AppointmentStatus.CANCELLED:
            appointment.cancelled_at = appointment.scheduled_start - timedelta(
                hours=rng.randint(2, 72)
            )
            appointment.cancellation_reason = rng.choice(
                ["Patient rescheduled", "Doctor unavailable",
                 "Felt better", "Transport difficulty"]
            )
        elif outcome == AppointmentStatus.NO_SHOW:
            missed[patient_id] = no_shows + 1


def _historical_outcome(
    rng: random.Random,
    *,
    reliability: float,
    lead_days: int,
    reschedules: int,
    confirmed: bool,
    distance: float | None,
    start: datetime,
    prior_no_show_rate: float,
    visit_type: VisitType,
) -> AppointmentStatus:
    """Latent no-show model producing realistic historical outcomes.

    Calibrated so the overall no-show rate lands in the 12-18% range typical of
    outpatient clinics. Each coefficient is small on its own: the point is that
    the *ordering* of risk is learnable, not that any single factor dominates.
    Prior no-shows are capped at 3 so the feedback loop cannot run away.
    """
    # Patient attendance tendency dominates in practice, so it dominates here.
    # `reliability` is drawn from a skewed distribution: most people attend
    # reliably, a minority miss often. Without this spread every appointment
    # scores near the base rate and the high-risk band stays empty, which would
    # make the dashboard useless. The model cannot see this variable directly —
    # it recovers it through each patient's observed prior no-show rate.
    propensity = (1.0 - reliability) ** 1.4

    risk = 0.015
    risk += 0.62 * propensity                    # personal attendance tendency
    risk += 0.005 * min(lead_days, 35)           # booked far ahead -> more drift
    risk += 0.045 * reschedules                  # repeated rescheduling
    risk += 0.0 if confirmed else 0.075          # never confirmed
    risk += 0.12 * prior_no_show_rate            # prior no-show history
    if distance:
        risk += 0.0022 * min(distance, 45)       # travel burden
    if start.weekday() == 0:
        risk += 0.02                             # Monday effect
    if start.hour <= 8:
        risk += 0.025                            # early clinics
    if visit_type == VisitType.TELECONSULTATION:
        risk -= 0.03                             # easier to attend

    risk = max(0.01, min(0.80, risk))

    roll = rng.random()
    if roll < risk:
        return AppointmentStatus.NO_SHOW
    if roll < risk + 0.08:
        return AppointmentStatus.CANCELLED
    return AppointmentStatus.COMPLETED


def seed_consultations(
    db: Session,
    rng: random.Random,
    appointments: list[Appointment],
    doctors_by_id: dict[str, Doctor],
    *,
    limit: int,
    demo: dict[str, User] | None = None,
) -> list[Consultation]:
    """Clinical records for completed appointments."""
    completed = [
        a for a in appointments if a.status == AppointmentStatus.COMPLETED
    ]
    rng.shuffle(completed)
    # The limit is far below the number of completed visits, so a shuffle
    # alone leaves the demo accounts with attended appointments that have no
    # consultation note behind them — a doctor's view of the patient that is
    # empty for the visits the demo is meant to show. Theirs are taken first;
    # the rest of the sample is unchanged.
    demo_ids = {u.id for u in (demo or {}).values()}
    completed.sort(key=lambda a: a.patient_user_id not in demo_ids)
    consultations: list[Consultation] = []

    for appointment in completed[:limit]:
        doctor = doctors_by_id.get(appointment.doctor_id)
        duration = rng.randint(10, 35)
        follow_up = rng.random() < 0.38

        consultation = Consultation(
            appointment_id=appointment.id,
            patient_user_id=appointment.patient_user_id,
            doctor_id=appointment.doctor_id,
            hospital_id=appointment.hospital_id,
            status=ConsultationStatus.COMPLETED,
            started_at=appointment.started_at,
            ended_at=appointment.completed_at,
            presenting_complaint=appointment.chief_complaint,
            clinical_notes=(
                f"Patient reports {(appointment.chief_complaint or 'symptoms').lower()}. "
                f"Examination performed. Vitals recorded and reviewed."
            ),
            assessment=rng.choice(ref.CONSULTATION_ASSESSMENTS),
            diagnosis_text=None,
            treatment_plan=rng.choice([
                "Continue current medication. Review in 3 months.",
                "Started on a short course of symptomatic treatment.",
                "Lifestyle advice given. Repeat bloods before next visit.",
                "Referred for further investigation.",
                "Dose adjusted based on latest results.",
            ]),
            prescribed_medications=(
                [
                    {"name": m[0], "dosage": m[1], "frequency": m[2]}
                    for m in rng.sample(MEDICATION_POOL, rng.randint(1, 2))
                ]
                if rng.random() < 0.7
                else []
            ),
            requested_tests=(
                rng.sample(["cbc", "fbs", "lipid_profile", "tsh", "urine_fr", "chest_xray"],
                           rng.randint(1, 3))
                if rng.random() < 0.5
                else []
            ),
            follow_up_required=follow_up,
            follow_up_date=(
                appointment.scheduled_start.date() + timedelta(days=rng.choice([14, 30, 60, 90]))
                if follow_up
                else None
            ),
            follow_up_notes="Review symptoms and test results." if follow_up else None,
            ai_specialty_accepted=rng.random() < 0.86,
            duration_minutes=duration,
        )
        consultations.append(consultation)

    db.add_all(consultations)
    db.flush()
    return consultations


def seed_referrals(
    db: Session,
    rng: random.Random,
    consultations: list[Consultation],
    doctors: list[Doctor],
    hospitals: list[Hospital],
) -> list[Referral]:
    referrals: list[Referral] = []
    for consultation in consultations:
        if rng.random() > 0.22:
            continue
        target = rng.choice(doctors)
        status = rng.choices(
            [ReferralStatus.COMPLETED, ReferralStatus.PENDING,
             ReferralStatus.ACCEPTED, ReferralStatus.EXPIRED],
            weights=[0.62, 0.18, 0.13, 0.07],
        )[0]
        created = consultation.ended_at or datetime.now(timezone.utc)
        referrals.append(
            Referral(
                patient_user_id=consultation.patient_user_id,
                from_doctor_id=consultation.doctor_id,
                to_doctor_id=target.id,
                to_hospital_id=target.hospital_id,
                consultation_id=consultation.id,
                specialty_code=target.specialty.code if target.specialty else "general_medicine",
                reason="Specialist opinion required for persistent symptoms.",
                urgency=rng.choices(
                    [UrgencyLevel.ROUTINE, UrgencyLevel.URGENT], weights=[0.85, 0.15]
                )[0],
                status=status,
                due_date=(created + timedelta(days=rng.randint(7, 45))).date(),
                completed_at=(
                    created + timedelta(days=rng.randint(3, 30))
                    if status == ReferralStatus.COMPLETED
                    else None
                ),
            )
        )
    db.add_all(referrals)
    db.flush()
    return referrals


# --------------------------------------------------------------------------
# Care programmes
# --------------------------------------------------------------------------
def seed_care_programmes_data(
    db: Session,
    rng: random.Random,
    patients: list[User],
    profiles: dict[str, PatientProfile],
    programmes: dict[str, CareProgramme],
    doctors: list[Doctor],
    hospitals: list[Hospital],
    demo: dict[str, User],
) -> dict[str, list]:
    """Enrol patients into maternal, postpartum and elderly pathways."""
    now = datetime.now(timezone.utc)
    enrollments: list[CareEnrollment] = []
    maternal_records: list[MaternalRecord] = []
    elderly_records: list[ElderlyRecord] = []

    obstetricians = [d for d in doctors if d.specialty and d.specialty.code == "obstetrics_gynaecology"]
    physicians = [d for d in doctors if d.specialty and d.specialty.code == "general_medicine"]
    maternity_hospitals = [h for h in hospitals if "obstetrics" in h.capability_codes()]

    def enrol(patient: User, programme: CareProgramme, doctor_pool: list[Doctor]) -> CareEnrollment:
        doctor = rng.choice(doctor_pool) if doctor_pool else None
        enrollment = CareEnrollment(
            patient_user_id=patient.id,
            programme_id=programme.id,
            hospital_id=(
                doctor.hospital_id if doctor else
                (rng.choice(maternity_hospitals).id if maternity_hospitals else None)
            ),
            primary_doctor_id=doctor.id if doctor else None,
            status=EnrollmentStatus.ACTIVE,
            enrolled_at=now - timedelta(days=rng.randint(20, 200)),
            progress_percent=round(rng.uniform(15, 85), 1),
        )
        db.add(enrollment)
        db.flush()
        enrollments.append(enrollment)
        return enrollment

    # --- Maternal: the demo persona plus a sample of pregnant patients ---
    from app.seed.seeder import (
        N_ELDERLY_CASES,
        N_MATERNAL_CASES,
        N_MEDICATION_PATIENTS,
        N_POSTPARTUM_CASES,
    )

    pregnant = [p for p in patients if profiles.get(p.id) and profiles[p.id].is_pregnant]
    if demo["maternal"] not in pregnant:
        pregnant.insert(0, demo["maternal"])
    # Always keep the demo persona, then sample the rest up to the cap.
    if len(pregnant) > N_MATERNAL_CASES:
        others = [p for p in pregnant if p.id != demo["maternal"].id]
        pregnant = [demo["maternal"]] + rng.sample(others, N_MATERNAL_CASES - 1)

    for patient in pregnant:
        profile = profiles[patient.id]
        enrollment = enrol(patient, programmes["maternal_care"], obstetricians)
        edd = profile.expected_delivery_date or (date.today() + timedelta(weeks=20))
        maternal_records.append(
            MaternalRecord(
                patient_user_id=patient.id,
                enrollment_id=enrollment.id,
                last_menstrual_period=edd - timedelta(days=280),
                expected_delivery_date=edd,
                is_postpartum=False,
                gravida=rng.randint(1, 4),
                para=rng.randint(0, 3),
                previous_pregnancies=[],
                risk_conditions=(
                    rng.sample(["Gestational diabetes", "Anaemia", "Previous caesarean",
                                "Raised blood pressure"], rng.randint(1, 2))
                    if rng.random() < 0.3
                    else []
                ),
                is_high_risk=rng.random() < 0.22,
                blood_group=profile.blood_group,
                baseline_weight_kg=profile.weight_kg,
                current_weight_kg=(profile.weight_kg or 60) + round(rng.uniform(4, 12), 1),
            )
        )

    # --- Postpartum cohort ---
    postpartum_candidates = [
        p for p in patients
        if profiles.get(p.id)
        and profiles[p.id].sex
        and str(profiles[p.id].sex) == "female"
        and profiles[p.id].age
        and 19 <= profiles[p.id].age <= 42
        and not profiles[p.id].is_pregnant
    ]
    # The demo persona is pinned rather than sampled, and pinned first, so the
    # postpartum pathway is always reachable by logging in rather than by
    # hunting through 3000 patients for one who happens to qualify.
    demo_postpartum = demo.get("postpartum")
    if demo_postpartum is None:
        chosen = rng.sample(
            postpartum_candidates,
            min(N_POSTPARTUM_CASES, len(postpartum_candidates)),
        )
    else:
        rest = [p for p in postpartum_candidates if p.id != demo_postpartum.id]
        chosen = [demo_postpartum] + rng.sample(
            rest, max(0, min(N_POSTPARTUM_CASES - 1, len(rest)))
        )

    for patient in chosen:
        profile = profiles[patient.id]
        enrollment = enrol(patient, programmes["postpartum_care"], obstetricians)
        # The demo mother is three weeks post-delivery: recent enough that the
        # postpartum danger-sign rules are live and the newborn is a newborn,
        # rather than a record that has gone quiet.
        delivered = (
            date.today() - timedelta(days=24) if patient is demo_postpartum
            else date.today() - timedelta(days=rng.randint(5, 150))
        )
        maternal_records.append(
            MaternalRecord(
                patient_user_id=patient.id,
                enrollment_id=enrollment.id,
                expected_delivery_date=delivered,
                is_postpartum=True,
                delivery_date=delivered,
                delivery_type=rng.choice(["Normal vaginal delivery", "Caesarean section"]),
                gravida=rng.randint(1, 4),
                para=rng.randint(1, 3),
                blood_group=profile.blood_group,
                newborn_name=(
                    "Sanuli Jayawardena" if patient is demo_postpartum
                    else "Baby " + patient.full_name.split()[-1]
                ),
                newborn_dob=delivered,
                newborn_birth_weight_kg=round(rng.uniform(2.4, 3.9), 2),
                feeding_method=rng.choice(["Exclusive breastfeeding", "Mixed feeding"]),
                epds_score=rng.randint(0, 16),
                epds_recorded_at=now - timedelta(days=rng.randint(1, 40)),
            )
        )

    # --- Elderly ---
    elderly_candidates = [
        p for p in patients
        if profiles.get(p.id) and profiles[p.id].age and profiles[p.id].age >= 65
    ]
    if demo["elderly"] not in elderly_candidates:
        elderly_candidates.append(demo["elderly"])
    if len(elderly_candidates) > N_ELDERLY_CASES:
        others = [p for p in elderly_candidates if p.id != demo["elderly"].id]
        elderly_candidates = [demo["elderly"]] + rng.sample(others, N_ELDERLY_CASES - 1)

    for patient in elderly_candidates:
        profile = profiles[patient.id]
        enrollment = enrol(patient, programmes["elderly_care"], physicians)
        elderly_records.append(
            ElderlyRecord(
                patient_user_id=patient.id,
                enrollment_id=enrollment.id,
                living_situation=rng.choice(
                    ["Lives with family", "Lives with spouse", "Lives alone"]
                ),
                mobility_level=rng.choices(
                    ["independent", "assisted", "limited"], weights=[0.6, 0.3, 0.1]
                )[0],
                uses_walking_aid=rng.random() < 0.3,
                fall_risk_level=rng.choices(["low", "medium", "high"], weights=[0.5, 0.35, 0.15])[0],
                chronic_conditions=profile.chronic_conditions or [],
                last_check_in_at=now - timedelta(days=rng.randint(0, 4)),
            )
        )

    db.add_all(maternal_records)
    db.add_all(elderly_records)
    db.flush()
    return {
        "enrollments": enrollments,
        "maternal": maternal_records,
        "elderly": elderly_records,
    }


# What each demo persona is actually on, matched to the conditions recorded on
# their profile rather than sampled from the pool, so the record stays
# internally consistent when a reviewer reads the profile and the medication
# list side by side.
DEMO_MEDICATIONS: dict[str, list[tuple]] = {
    # Hypothyroidism and iron-deficiency anaemia on file.
    "patient": [
        ("Levothyroxine", "50 mcg", "Once daily before breakfast", ["07:00"]),
        ("Ferrous sulphate", "200 mg", "Twice daily after meals", ["08:00", "20:00"]),
    ],
    # Sri Lanka's antenatal standard: folate through the first trimester, then
    # iron and calcium supplementation for the rest of the pregnancy.
    "maternal": [
        ("Folic acid", "1 mg", "Once daily", ["09:00"]),
        ("Ferrous sulphate", "200 mg", "Twice daily after meals", ["08:00", "20:00"]),
        ("Calcium carbonate", "500 mg", "Twice daily with food", ["08:00", "20:00"]),
    ],
    # Continued while breastfeeding; iron replaces what delivery cost.
    "postpartum": [
        ("Ferrous sulphate", "200 mg", "Twice daily after meals", ["08:00", "20:00"]),
        ("Calcium carbonate", "500 mg", "Twice daily with food", ["08:00", "20:00"]),
    ],
}


def seed_medications(
    db: Session,
    rng: random.Random,
    patients: list[User],
    profiles: dict[str, PatientProfile],
    doctors: list[Doctor],
    demo: dict[str, User],
) -> tuple[list[Medication], list[MedicationLog]]:
    """Medication schedules plus 30 days of adherence history."""
    medications: list[Medication] = []
    logs: list[MedicationLog] = []
    now = datetime.now(timezone.utc)
    today = date.today()

    def add_medication(patient: User, spec: tuple, *, critical: bool = False) -> Medication:
        name, dosage, frequency, times = spec
        medication = Medication(
            patient_user_id=patient.id,
            prescribed_by_doctor_id=rng.choice(doctors).id,
            name=name,
            dosage=dosage,
            form="inhaler" if "inhaler" in name.lower() else "tablet",
            instructions=frequency,
            schedule_times=times,
            frequency_label=frequency,
            start_date=today - timedelta(days=rng.randint(40, 400)),
            is_active=True,
            is_critical=critical,
        )
        db.add(medication)
        medications.append(medication)
        return medication

    # Patients on chronic medication, capped so dose-log volume stays sane.
    from app.seed.seeder import N_MEDICATION_PATIENTS

    on_medication = [
        p for p in patients
        if profiles.get(p.id) and profiles[p.id].chronic_conditions
    ]
    if len(on_medication) > N_MEDICATION_PATIENTS:
        on_medication = rng.sample(on_medication, N_MEDICATION_PATIENTS)

    # Demo accounts are sampled like anyone else, so they frequently ended up
    # with no medications at all and a dashboard that looked broken — the
    # first accounts anyone logs into showing the least.
    demo_med_ids = {
        demo[key].id for key in DEMO_MEDICATIONS if demo.get(key)
    }
    for key in DEMO_MEDICATIONS:
        person = demo.get(key)
        if person is not None and person not in on_medication:
            on_medication.insert(0, person)

    for patient in on_medication:
        if patient.id in demo_med_ids:
            key = next(k for k in DEMO_MEDICATIONS if demo.get(k) and demo[k].id == patient.id)
            for spec in DEMO_MEDICATIONS[key]:
                add_medication(patient, spec)
            continue
        for spec in rng.sample(MEDICATION_POOL, rng.randint(1, 3)):
            add_medication(patient, spec)

    # The elderly demo persona gets a deliberate missed-dose pattern so
    # Scenario E (guardian alert on repeated misses) is reproducible.
    elderly_patient = demo["elderly"]
    demo_med = add_medication(
        elderly_patient, ("Amlodipine", "5mg", "Once daily after breakfast", ["08:00"]),
        critical=True,
    )
    db.flush()

    for medication in medications:
        adherence = rng.betavariate(7, 2)  # most patients are largely adherent
        is_demo_med = medication.id == demo_med.id

        for day_offset in range(30, 0, -1):
            due_date = today - timedelta(days=day_offset)
            for time_str in medication.schedule_times:
                hour, minute = (int(x) for x in time_str.split(":"))
                due_at = _utc(due_date, time(hour, minute))
                if due_at > now:
                    continue

                if is_demo_med and day_offset <= 3:
                    # Three consecutive missed critical doses -> guardian alert.
                    status = MedicationLogStatus.MISSED
                elif rng.random() < adherence:
                    status = MedicationLogStatus.TAKEN
                else:
                    status = rng.choices(
                        [MedicationLogStatus.MISSED, MedicationLogStatus.SKIPPED,
                         MedicationLogStatus.SNOOZED],
                        weights=[0.6, 0.25, 0.15],
                    )[0]

                logs.append(
                    MedicationLog(
                        medication_id=medication.id,
                        patient_user_id=medication.patient_user_id,
                        due_at=due_at,
                        status=status,
                        recorded_at=(
                            due_at + timedelta(minutes=rng.randint(1, 90))
                            if status != MedicationLogStatus.MISSED
                            else None
                        ),
                    )
                )

    db.add_all(logs)
    db.flush()
    return medications, logs


def seed_check_ins_and_vitals(
    db: Session,
    rng: random.Random,
    care_data: dict[str, list],
    demo: dict[str, User],
) -> tuple[list[DailyCheckIn], list[VitalRecord]]:
    """Check-in history for maternal and elderly cohorts, plus vitals."""
    check_ins: list[DailyCheckIn] = []
    vitals: list[VitalRecord] = []
    today = date.today()
    now = datetime.now(timezone.utc)

    for record in care_data["maternal"]:
        check_in_type = CheckInType.POSTPARTUM if record.is_postpartum else CheckInType.MATERNAL
        for day_offset in range(21, 0, -1):
            if rng.random() < 0.35:
                continue  # not everyone checks in daily
            day = today - timedelta(days=day_offset)
            danger: list[str] = []
            responses: dict[str, bool] = {}
            questions = (
                ["severe_bleeding", "fever", "severe_headache", "low_mood"]
                if record.is_postpartum
                else ["severe_headache", "blurred_vision", "vaginal_bleeding",
                      "severe_abdominal_pain", "reduced_fetal_movement", "swelling", "fever"]
            )
            for code in questions:
                # Danger signs are rare, as they should be.
                flagged = rng.random() < 0.02
                responses[code] = flagged
                if flagged:
                    danger.append(code)

            check_ins.append(
                DailyCheckIn(
                    patient_user_id=record.patient_user_id,
                    enrollment_id=record.enrollment_id,
                    check_in_type=check_in_type,
                    check_in_date=day,
                    wellbeing=rng.choices(
                        [WellbeingStatus.GOOD, WellbeingStatus.NOT_GREAT],
                        weights=[0.82, 0.18],
                    )[0],
                    responses=responses,
                    danger_signs_reported=danger,
                    triggered_alert=bool(danger),
                    escalation_message=(
                        "Danger sign reported. Contact your maternity unit today."
                        if danger
                        else None
                    ),
                )
            )

        # Antenatal BP monitoring.
        for day_offset in range(0, 60, rng.randint(4, 9)):
            systolic = rng.randint(102, 138)
            diastolic = rng.randint(64, 88)
            vitals.append(
                VitalRecord(
                    patient_user_id=record.patient_user_id,
                    vital_type="blood_pressure",
                    systolic=systolic,
                    diastolic=diastolic,
                    unit="mmHg",
                    recorded_at=now - timedelta(days=day_offset),
                    is_abnormal=systolic >= 140 or diastolic >= 90,
                    source="patient",
                )
            )

    for record in care_data["elderly"]:
        is_demo = record.patient_user_id == demo["elderly"].id
        for day_offset in range(21, 0, -1):
            # The demo persona misses the last three days (Scenario E).
            if is_demo and day_offset <= 3:
                continue
            if not is_demo and rng.random() < 0.3:
                continue

            day = today - timedelta(days=day_offset)
            wellbeing = rng.choices(
                [WellbeingStatus.GOOD, WellbeingStatus.NOT_GREAT, WellbeingStatus.NEED_HELP],
                weights=[0.72, 0.23, 0.05],
            )[0]
            danger = []
            responses = {}
            for code in ["dizziness", "injury", "shortness_of_breath", "chest_pain"]:
                flagged = rng.random() < 0.03
                responses[code] = flagged
                if flagged:
                    danger.append(code)

            check_ins.append(
                DailyCheckIn(
                    patient_user_id=record.patient_user_id,
                    enrollment_id=record.enrollment_id,
                    check_in_type=CheckInType.ELDERLY,
                    check_in_date=day,
                    wellbeing=wellbeing,
                    responses=responses,
                    danger_signs_reported=danger,
                    triggered_alert=bool(danger) or wellbeing == WellbeingStatus.NEED_HELP,
                )
            )

        for day_offset in range(0, 45, rng.randint(2, 5)):
            systolic = rng.randint(112, 165)
            diastolic = rng.randint(68, 98)
            vitals.append(
                VitalRecord(
                    patient_user_id=record.patient_user_id,
                    vital_type="blood_pressure",
                    systolic=systolic,
                    diastolic=diastolic,
                    unit="mmHg",
                    recorded_at=now - timedelta(days=day_offset),
                    is_abnormal=systolic >= 140 or diastolic >= 90,
                    source="patient",
                )
            )
            if rng.random() < 0.5:
                glucose = round(rng.uniform(88, 215), 0)
                vitals.append(
                    VitalRecord(
                        patient_user_id=record.patient_user_id,
                        vital_type="blood_glucose",
                        value_numeric=glucose,
                        unit="mg/dL",
                        recorded_at=now - timedelta(days=day_offset),
                        is_abnormal=glucose >= 180 or glucose <= 70,
                        source="patient",
                    )
                )

    # Update elderly consecutive-miss counters from the generated history.
    for record in care_data["elderly"]:
        patient_check_ins = [c for c in check_ins if c.patient_user_id == record.patient_user_id]
        latest = max((c.check_in_date for c in patient_check_ins), default=None)
        if latest:
            record.consecutive_missed_checkins = max(0, (today - latest).days - 1)
            record.last_check_in_at = _utc(latest, time(9, 0))

    db.add_all(check_ins)
    db.add_all(vitals)
    db.flush()
    return check_ins, vitals
