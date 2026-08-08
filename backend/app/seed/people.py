"""Seeds users: system admin, hospital admins, doctors, patients, guardians.

Also creates the fixed demo accounts used by the walkthrough scenarios.
"""

from __future__ import annotations

import random
from datetime import date, time, timedelta

from sqlalchemy.orm import Session

from app.clinical.catalog import SPECIALTY_BY_CODE
from app.models import (
    Doctor,
    DoctorSchedule,
    GuardianPermission,
    GuardianRelationship,
    Hospital,
    PatientProfile,
    Specialty,
    User,
)
from app.models.enums import (
    GuardianPermissionType,
    Language,
    Sex,
    UserRole,
    VerificationStatus,
    VisitType,
)
from app.seed import reference as ref

CHRONIC_POOL = [
    "Type 2 Diabetes", "Hypertension", "Hypothyroidism", "Asthma",
    "Iron deficiency anaemia", "Dyslipidaemia", "Osteoarthritis",
    "Chronic kidney disease (stage 2)", "Migraine", "GERD",
]
ALLERGY_POOL = [
    "Penicillin", "Sulfa drugs", "Peanuts", "Shellfish", "Dust mites",
    "Aspirin", "Latex",
]
MEDICATION_POOL = [
    ("Metformin", "500mg", "Twice daily", ["08:00", "20:00"]),
    ("Levothyroxine", "75mcg", "Once daily before breakfast", ["07:00"]),
    ("Amlodipine", "5mg", "Once daily", ["08:00"]),
    ("Atorvastatin", "10mg", "Once at night", ["21:00"]),
    ("Losartan", "50mg", "Once daily", ["08:00"]),
    ("Salbutamol inhaler", "2 puffs", "As needed", ["08:00", "20:00"]),
    ("Ferrous sulphate", "200mg", "Once daily after food", ["13:00"]),
    ("Omeprazole", "20mg", "Once daily before breakfast", ["07:00"]),
]


def seed_admins(
    db: Session, rng: random.Random, password_hash: str, hospitals: list[Hospital]
) -> tuple[User, list[User]]:
    """One SuwaPath system admin plus hospital administrators."""
    from app.seed.seeder import make_email, make_name, make_phone

    system_admin = User(
        email="admin@suwapath.lk",
        phone=make_phone(),
        hashed_password=password_hash,
        full_name="Ravindu Wickramasinghe",
        role=UserRole.SYSTEM_ADMIN,
        preferred_language=Language.EN,
    )
    db.add(system_admin)

    hospital_admins: list[User] = []
    # The first admin is the fixed demo login at the flagship hospital.
    demo_admin = User(
        email="hospital@suwapath.lk",
        phone=make_phone(),
        hashed_password=password_hash,
        full_name="Chathurika Bandara",
        role=UserRole.HOSPITAL_ADMIN,
        preferred_language=Language.EN,
        hospital_id=hospitals[0].id,
    )
    db.add(demo_admin)
    hospital_admins.append(demo_admin)

    from app.seed.seeder import N_HOSPITAL_ADMINS

    for index in range(N_HOSPITAL_ADMINS - 1):
        sex = rng.choice([Sex.MALE, Sex.FEMALE])
        name = make_name(sex)
        hospital = hospitals[(index + 1) % len(hospitals)]
        admin = User(
            email=make_email(name, "suwapath.lk"),
            phone=make_phone(),
            hashed_password=password_hash,
            full_name=name,
            role=UserRole.HOSPITAL_ADMIN,
            preferred_language=Language.EN,
            hospital_id=hospital.id,
        )
        db.add(admin)
        hospital_admins.append(admin)

    db.flush()
    return system_admin, hospital_admins


def seed_doctors(
    db: Session,
    rng: random.Random,
    password_hash: str,
    hospitals: list[Hospital],
    specialties: dict[str, Specialty],
) -> list[Doctor]:
    """70 doctors distributed across specialties, with weekly schedules."""
    from app.seed.seeder import make_email, make_name, make_phone

    doctors: list[Doctor] = []
    # Hospitals with more consultation rooms attract proportionally more doctors.
    weights = [max(1, h.consultation_rooms) for h in hospitals]

    demo_created = False
    for specialty_code, count in ref.SPECIALTY_DOCTOR_COUNTS.items():
        spec_def = SPECIALTY_BY_CODE[specialty_code]
        for _ in range(count):
            sex = rng.choice([Sex.MALE, Sex.FEMALE])
            hospital = rng.choices(hospitals, weights=weights)[0]

            if not demo_created and specialty_code == "endocrinology":
                # Fixed demo doctor login, matching the product walkthrough.
                name = "Dileepa Perera"
                email = "doctor@suwapath.lk"
                hospital = hospitals[0]
                sex = Sex.MALE
                demo_created = True
            else:
                name = make_name(sex)
                email = make_email(f"dr {name}", "suwapath.lk")

            languages = ["en"]
            if rng.random() < 0.85:
                languages.append("si")
            if rng.random() < 0.35:
                languages.append("ta")

            user = User(
                email=email,
                phone=make_phone(),
                hashed_password=password_hash,
                full_name=f"Dr. {name}",
                role=UserRole.DOCTOR,
                preferred_language=Language.EN,
                hospital_id=hospital.id,
            )
            db.add(user)
            db.flush()

            experience = rng.randint(3, 32)
            base_fee = {
                "general_medicine": 2500, "emergency_medicine": 3000,
                "sexual_health": 3500,
            }.get(specialty_code, 4000)
            fee = base_fee + experience * 90 + rng.choice([0, 250, 500])

            doctor = Doctor(
                user_id=user.id,
                hospital_id=hospital.id,
                specialty_id=specialties[specialty_code].id,
                slmc_registration_no=f"SLMC/{rng.randint(20000, 79999)}",
                sub_specialty=(
                    rng.choice(spec_def.sub_specialties)
                    if spec_def.sub_specialties and rng.random() < 0.6
                    else None
                ),
                qualifications=rng.choice(ref.QUALIFICATIONS),
                years_experience=experience,
                languages=languages,
                bio=(
                    f"{spec_def.name} specialist with {experience} years of "
                    f"clinical experience, practising at {hospital.name}."
                ),
                supports_teleconsultation=rng.random() < 0.75,
                supports_physical=True,
                consultation_fee_lkr=float(round(fee, -1)),
                teleconsultation_fee_lkr=float(round(fee * 0.75, -1)),
                verification_status=(
                    VerificationStatus.VERIFIED
                    if rng.random() < 0.93
                    else VerificationStatus.PENDING
                ),
                rating=round(rng.uniform(3.7, 4.9), 1),
                total_consultations=rng.randint(120, 4200),
                accepts_new_patients=rng.random() < 0.92,
            )
            db.add(doctor)
            db.flush()

            # The fixed demo doctor gets full weekday coverage so their live
            # queue is never empty on the day of a demo, whatever weekday it is.
            _add_schedules(
                db, rng, doctor, hospital, full_week=email == "doctor@suwapath.lk"
            )
            doctors.append(doctor)

    db.flush()
    return doctors


def _add_schedules(
    db: Session,
    rng: random.Random,
    doctor: Doctor,
    hospital: Hospital,
    *,
    full_week: bool = False,
) -> None:
    """Give each doctor 3-5 weekly clinic blocks, plus optional tele sessions."""
    working_days = (
        list(range(0, 6)) if full_week else rng.sample(range(0, 6), rng.randint(3, 5))
    )
    for day in working_days:
        morning = True if full_week else rng.random() < 0.7
        if morning:
            start, end = time(8, 0), time(12, 30)
        else:
            start, end = time(14, 0), time(18, 30)

        db.add(
            DoctorSchedule(
                doctor_id=doctor.id,
                hospital_id=hospital.id,
                day_of_week=day,
                start_time=start,
                end_time=end,
                slot_duration_minutes=rng.choice([15, 20, 30]),
                visit_type=str(VisitType.PHYSICAL),
                max_patients=rng.randint(12, 22),
            )
        )
        if full_week:
            # An afternoon clinic too, so the demo queue has depth.
            db.add(
                DoctorSchedule(
                    doctor_id=doctor.id,
                    hospital_id=hospital.id,
                    day_of_week=day,
                    start_time=time(14, 0),
                    end_time=time(17, 30),
                    slot_duration_minutes=20,
                    visit_type=str(VisitType.PHYSICAL),
                    max_patients=10,
                )
            )

    if doctor.supports_teleconsultation:
        for day in rng.sample(range(0, 7), rng.randint(1, 3)):
            db.add(
                DoctorSchedule(
                    doctor_id=doctor.id,
                    hospital_id=hospital.id,
                    day_of_week=day,
                    start_time=time(19, 0),
                    end_time=time(21, 0),
                    slot_duration_minutes=20,
                    visit_type=str(VisitType.TELECONSULTATION),
                    max_patients=6,
                )
            )


def seed_patients(
    db: Session, rng: random.Random, password_hash: str
) -> tuple[list[User], dict[str, User]]:
    """300 patients including the four fixed demo personas."""
    from app.seed.seeder import N_APP_PATIENTS, N_PATIENTS, make_email, make_name, make_phone

    patients: list[User] = []
    demo: dict[str, User] = {}

    # --- Fixed demo personas (referenced by the demo scenarios) ---
    demo_specs = [
        {
            "key": "patient",
            "email": "patient@suwapath.lk",
            "name": "Nimali Fernando",
            "sex": Sex.FEMALE,
            "dob": date(1994, 3, 18),
            "city": "Colombo",
            "chronic": ["Hypothyroidism", "Iron deficiency anaemia"],
            "allergies": [],
            "language": Language.EN,
        },
        {
            "key": "maternal",
            "email": "maternal@suwapath.lk",
            "name": "Dilini Fernando",
            "sex": Sex.FEMALE,
            "dob": date(1998, 7, 5),
            "city": "Colombo",
            "chronic": [],
            "allergies": ["Penicillin"],
            "language": Language.SI,
        },
        {
            "key": "elderly",
            "email": "elderly@suwapath.lk",
            "name": "Sunil Fernando",
            "sex": Sex.MALE,
            "dob": date(1957, 1, 22),
            "city": "Nugegoda",
            "chronic": ["Hypertension", "Type 2 Diabetes", "Osteoarthritis"],
            "allergies": ["Aspirin"],
            "language": Language.SI,
        },
    ]

    for spec in demo_specs:
        loc = ref.LOCATION_BY_CITY[spec["city"]]
        lat, lon = _jitter(rng, loc)
        user = User(
            email=spec["email"],
            phone=make_phone(),
            hashed_password=password_hash,
            full_name=spec["name"],
            role=UserRole.PATIENT,
            preferred_language=spec["language"],
        )
        db.add(user)
        db.flush()

        is_elderly = spec["key"] == "elderly"
        profile = PatientProfile(
            user_id=user.id,
            date_of_birth=spec["dob"],
            sex=spec["sex"],
            blood_group=rng.choice(["A+", "B+", "O+", "AB+", "O-"]),
            city=loc.city,
            district=loc.district,
            address=f"{rng.randint(1, 220)}, {rng.choice(['Flower Road', 'Lake Drive', 'Hill Street'])}, {loc.city}",
            latitude=lat,
            longitude=lon,
            height_cm=float(rng.randint(150, 178)),
            weight_kg=float(rng.randint(48, 88)),
            chronic_conditions=spec["chronic"],
            allergies=spec["allergies"],
            current_medications=[],
            is_pregnant=spec["key"] == "maternal",
            expected_delivery_date=(
                date.today() + timedelta(weeks=12) if spec["key"] == "maternal" else None
            ),
            emergency_contact_name="Nimal Fernando",
            emergency_contact_phone=make_phone(),
            accessibility_large_text=is_elderly,
        )
        db.add(profile)
        patients.append(user)
        demo[spec["key"]] = user

    # --- Remaining population ---
    # The first N_APP_PATIENTS are the fully-profiled SuwaPath app users; the
    # remainder represent the hospitals' wider historical patient base.
    for _ in range(N_PATIENTS - len(demo_specs)):
        sex = rng.choices([Sex.FEMALE, Sex.MALE], weights=[0.53, 0.47])[0]
        name = make_name(sex)
        loc = rng.choices(
            ref.LOCATIONS, weights=[l.population_weight for l in ref.LOCATIONS]
        )[0]
        lat, lon = _jitter(rng, loc)

        # Age distribution skewed toward working-age adults with an elderly tail.
        age = rng.choices(
            [rng.randint(1, 12), rng.randint(13, 29), rng.randint(30, 49),
             rng.randint(50, 64), rng.randint(65, 88)],
            weights=[0.10, 0.22, 0.31, 0.22, 0.15],
        )[0]
        dob = date.today() - timedelta(days=age * 365 + rng.randint(0, 364))

        chronic = (
            rng.sample(CHRONIC_POOL, rng.randint(1, 3))
            if age > 45 and rng.random() < 0.62
            else (rng.sample(CHRONIC_POOL, 1) if rng.random() < 0.15 else [])
        )
        is_pregnant = (
            sex == Sex.FEMALE and 18 <= age <= 42 and rng.random() < 0.07
        )

        user = User(
            email=make_email(name),
            phone=make_phone(),
            hashed_password=password_hash,
            full_name=name,
            role=UserRole.PATIENT,
            preferred_language=rng.choices(
                [Language.EN, Language.SI, Language.TA], weights=[0.45, 0.42, 0.13]
            )[0],
        )
        db.add(user)
        db.flush()

        db.add(
            PatientProfile(
                user_id=user.id,
                date_of_birth=dob,
                sex=sex,
                blood_group=rng.choice(["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"]),
                city=loc.city,
                district=loc.district,
                address=f"{rng.randint(1, 480)}, {rng.choice(['Main Street', 'Temple Road', 'School Lane', 'Beach Road', 'Park Avenue'])}, {loc.city}",
                latitude=lat,
                longitude=lon,
                height_cm=float(rng.randint(145, 185)),
                weight_kg=float(rng.randint(40, 96)),
                chronic_conditions=chronic,
                allergies=rng.sample(ALLERGY_POOL, 1) if rng.random() < 0.22 else [],
                current_medications=[],
                is_pregnant=is_pregnant,
                expected_delivery_date=(
                    date.today() + timedelta(weeks=rng.randint(2, 34))
                    if is_pregnant
                    else None
                ),
                emergency_contact_name=make_name(rng.choice([Sex.MALE, Sex.FEMALE])),
                emergency_contact_phone=make_phone(),
                accessibility_large_text=age >= 70,
            )
        )
        patients.append(user)

    db.flush()
    return patients, demo


def _jitter(rng: random.Random, loc: ref.Location) -> tuple[float, float]:
    return (
        round(loc.latitude + rng.uniform(-0.035, 0.035), 6),
        round(loc.longitude + rng.uniform(-0.035, 0.035), 6),
    )


def seed_guardians(
    db: Session,
    rng: random.Random,
    password_hash: str,
    patients: list[User],
    profiles: dict[str, PatientProfile],
    demo: dict[str, User],
) -> list[User]:
    """120 guardian relationships with explicit, varied consent scopes."""
    from app.seed.seeder import N_GUARDIAN_LINKS, make_email, make_name, make_phone

    guardians: list[User] = []

    # --- Fixed demo guardian: linked to both the elderly and maternal personas ---
    demo_guardian = User(
        email="guardian@suwapath.lk",
        phone=make_phone(),
        hashed_password=password_hash,
        full_name="Nimal Fernando",
        role=UserRole.GUARDIAN,
        preferred_language=Language.EN,
    )
    db.add(demo_guardian)
    db.flush()
    guardians.append(demo_guardian)

    # Father on the elderly pathway — full oversight including reports.
    _link(
        db, demo_guardian, demo["elderly"], "Father",
        [
            GuardianPermissionType.APPOINTMENTS,
            GuardianPermissionType.MEDICATIONS,
            GuardianPermissionType.REMINDERS,
            GuardianPermissionType.WELLBEING,
            GuardianPermissionType.CARE_PROGRAMME,
            GuardianPermissionType.EMERGENCY_ALERTS,
            GuardianPermissionType.REPORTS,
        ],
        can_book=True,
    )
    # Wife on the maternal pathway — deliberately *without* REPORTS, so the
    # consent boundary is visible in the demo.
    _link(
        db, demo_guardian, demo["maternal"], "Wife",
        [
            GuardianPermissionType.APPOINTMENTS,
            GuardianPermissionType.REMINDERS,
            GuardianPermissionType.WELLBEING,
            GuardianPermissionType.CARE_PROGRAMME,
            GuardianPermissionType.EMERGENCY_ALERTS,
        ],
        can_book=True,
    )

    # --- Generated relationships ---
    eligible = [p for p in patients if p.id not in {u.id for u in demo.values()}]
    rng.shuffle(eligible)
    linked = 0
    index = 0

    while linked < N_GUARDIAN_LINKS - 2 and index < len(eligible):
        dependent = eligible[index]
        index += 1
        profile = profiles.get(dependent.id)
        if not profile:
            continue

        age = profile.age or 40
        # Guardianship concentrates on children, elderly and pregnant patients.
        if age <= 16:
            label = rng.choice(["Mother", "Father"])
        elif age >= 65:
            label = rng.choice(["Son", "Daughter"])
        elif profile.is_pregnant:
            label = "Husband"
        elif rng.random() < 0.12:
            label = rng.choice(["Spouse", "Sibling"])
        else:
            continue

        sex = Sex.FEMALE if label in ("Mother", "Daughter") else Sex.MALE
        name = make_name(sex)
        guardian = User(
            email=make_email(name),
            phone=make_phone(),
            hashed_password=password_hash,
            full_name=name,
            role=UserRole.GUARDIAN,
            preferred_language=rng.choices(
                [Language.EN, Language.SI, Language.TA], weights=[0.45, 0.42, 0.13]
            )[0],
        )
        db.add(guardian)
        db.flush()

        # Consent varies: most grant the basics, a minority grant everything.
        scopes = [
            GuardianPermissionType.APPOINTMENTS,
            GuardianPermissionType.REMINDERS,
            GuardianPermissionType.EMERGENCY_ALERTS,
        ]
        if rng.random() < 0.7:
            scopes.append(GuardianPermissionType.MEDICATIONS)
        if rng.random() < 0.6:
            scopes.append(GuardianPermissionType.WELLBEING)
        if rng.random() < 0.5:
            scopes.append(GuardianPermissionType.CARE_PROGRAMME)
        if rng.random() < 0.3:
            scopes.append(GuardianPermissionType.REPORTS)
        if rng.random() < 0.12:
            scopes.append(GuardianPermissionType.FULL_MEDICAL)

        _link(db, guardian, dependent, label, scopes, can_book=rng.random() < 0.55)
        guardians.append(guardian)
        linked += 1

    # Second pass: the age-based criteria above are selective, so top up with
    # spouse/sibling links until the target relationship count is reached.
    already_linked = {
        rel.patient_user_id
        for rel in db.query(GuardianRelationship).all()
    }
    for dependent in eligible:
        if linked >= N_GUARDIAN_LINKS - 2:
            break
        if dependent.id in already_linked:
            continue

        label = rng.choice(["Spouse", "Sibling", "Son", "Daughter"])
        sex = Sex.FEMALE if label in ("Daughter",) else Sex.MALE
        name = make_name(sex)
        guardian = User(
            email=make_email(name),
            phone=make_phone(),
            hashed_password=password_hash,
            full_name=name,
            role=UserRole.GUARDIAN,
            preferred_language=Language.EN,
        )
        db.add(guardian)
        db.flush()

        scopes = [
            GuardianPermissionType.APPOINTMENTS,
            GuardianPermissionType.REMINDERS,
            GuardianPermissionType.EMERGENCY_ALERTS,
        ]
        if rng.random() < 0.6:
            scopes.append(GuardianPermissionType.MEDICATIONS)
        if rng.random() < 0.45:
            scopes.append(GuardianPermissionType.WELLBEING)

        _link(db, guardian, dependent, label, scopes, can_book=rng.random() < 0.5)
        guardians.append(guardian)
        already_linked.add(dependent.id)
        linked += 1

    db.flush()
    return guardians


def _link(
    db: Session,
    guardian: User,
    dependent: User,
    label: str,
    scopes: list[GuardianPermissionType],
    *,
    can_book: bool,
) -> GuardianRelationship:
    from datetime import datetime, timezone

    relationship = GuardianRelationship(
        guardian_user_id=guardian.id,
        patient_user_id=dependent.id,
        relationship_label=label,
        is_active=True,
        can_book_appointments=can_book,
    )
    db.add(relationship)
    db.flush()

    now = datetime.now(timezone.utc)
    for scope in scopes:
        db.add(
            GuardianPermission(
                relationship_id=relationship.id,
                permission=scope,
                granted=True,
                granted_at=now,
            )
        )
    return relationship
