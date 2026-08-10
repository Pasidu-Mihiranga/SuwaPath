"""Who can see and approve a proposal.

`approve()` used to assume that any approver who was not the patient must be a
guardian, so a doctor or an administrator approving something addressed to
them was told "You are not registered as a guardian for this person". Every
cross-role edge was unreachable, and no test noticed because no proposal had
ever been addressed to anyone but a patient.

The negatives here matter more than the positives. A doctor must *not* see a
proposal merely because it concerns their patient, and a system administrator
must *not* see everything — that is a PHI hole with an administrative excuse,
and it is the natural shape of the bug if someone later "simplifies" the
visibility clause.

    uvicorn app.main:app --port 8000
    python tests/test_roles.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import actions as registry  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.models.agentic import ActionProposal  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.providers import Doctor  # noqa: E402

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api/v1"
PASSWORD = "Demo@1234"
MARKER = "roles-suite-fixture"

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    (PASSED if condition else FAILED).append(label)
    print(f"    {'PASS' if condition else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    return condition


def section(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def login(client: httpx.Client, email: str) -> dict:
    r = client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def cleanup(db) -> None:
    db.query(ActionProposal).filter(
        ActionProposal.idempotency_key.like(f"{MARKER}%")
    ).delete(synchronize_session=False)
    db.commit()


def main() -> int:
    db = SessionLocal()
    client = httpx.Client(timeout=30.0)
    try:
        cleanup(db)

        patient = db.query(User).filter(User.email == "patient@suwapath.lk").one()
        doctor = db.query(User).filter(User.email == "doctor@suwapath.lk").one()
        admin = db.query(User).filter(User.email == "hospital@suwapath.lk").one()
        sysadmin = db.query(User).filter(User.email == "admin@suwapath.lk").one()
        other = db.query(User).filter(User.email == "maternal@suwapath.lk").one()

        section("Setup — one proposal addressed to each non-patient role")

        # Addressed to the doctor, about the patient.
        registry.propose(
            db,
            subject=patient,
            audience=doctor,
            action_name="schedule_reminder",
            args={"title": "Follow-up", "body": "Please review."},
            title="Review this patient",
            preview_text="A follow-up has lapsed.",
            evidence={"detector_id": MARKER},
            idempotency_key=f"{MARKER}:doctor",
        )
        # Addressed to a role claim scoped to the admin's hospital.
        registry.propose(
            db,
            audience_role=UserRole.HOSPITAL_ADMIN,
            audience_scope_id=admin.hospital_id,
            action_name="send_appointment_reminders",
            args={"appointment_ids": []},
            title="Tomorrow's high-risk appointments",
            preview_text="12 appointments are high risk.",
            evidence={"detector_id": MARKER},
            idempotency_key=f"{MARKER}:hospital",
        )
        # Addressed to the platform.
        registry.propose(
            db,
            audience_role=UserRole.SYSTEM_ADMIN,
            action_name="reindex_provider_directory",
            args={},
            title="Reindex the provider directory",
            preview_text="3 providers were verified since the last build.",
            evidence={"detector_id": MARKER},
            idempotency_key=f"{MARKER}:sysadmin",
        )
        made = (
            db.query(ActionProposal)
            .filter(ActionProposal.idempotency_key.like(f"{MARKER}%"))
            .all()
        )
        check("Three role-addressed proposals created", len(made) == 3, f"{len(made)}")
        by_key = {p.idempotency_key.split(":")[-1]: p for p in made}

        check(
            "An operational proposal needs no patient",
            by_key["hospital"].subject_user_id is None,
        )

        section("Visibility — each role sees only what is theirs")

        def ids_for(email: str) -> set[str]:
            headers = login(client, email)
            r = client.get(f"{API}/actions", headers=headers)
            return {p["id"] for p in r.json().get("proposals", [])}

        doctor_sees = ids_for("doctor@suwapath.lk")
        admin_sees = ids_for("hospital@suwapath.lk")
        sysadmin_sees = ids_for("admin@suwapath.lk")
        patient_sees = ids_for("patient@suwapath.lk")
        other_sees = ids_for("maternal@suwapath.lk")

        check("Doctor sees the proposal addressed to them",
              by_key["doctor"].id in doctor_sees)
        check("Hospital admin sees their hospital's role claim",
              by_key["hospital"].id in admin_sees)
        check("System admin sees the platform role claim",
              by_key["sysadmin"].id in sysadmin_sees)

        # The negatives.
        check(
            "Patient does NOT see a proposal addressed to their doctor",
            by_key["doctor"].id not in patient_sees,
        )
        check(
            "Doctor does NOT see the hospital or platform claims",
            by_key["hospital"].id not in doctor_sees
            and by_key["sysadmin"].id not in doctor_sees,
        )
        check(
            "System admin does NOT see everything",
            by_key["doctor"].id not in sysadmin_sees
            and by_key["hospital"].id not in sysadmin_sees,
            f"{len(sysadmin_sees)} visible",
        )
        check(
            "An unrelated patient sees none of them",
            not (other_sees & {p.id for p in made}),
        )

        section("Approval — the check that used to 403 for every role")

        doctor_headers = login(client, "doctor@suwapath.lk")
        treating = (
            db.query(Doctor).filter(Doctor.user_id == doctor.id).one_or_none()
        )
        r = client.post(
            f"{API}/actions/{by_key['doctor'].id}/approve", headers=doctor_headers
        )
        check(
            "Doctor can approve a proposal addressed to them",
            r.status_code == 200,
            f"HTTP {r.status_code} {r.text[:90]}",
        )

        admin_headers = login(client, "hospital@suwapath.lk")
        r = client.post(
            f"{API}/actions/{by_key['hospital'].id}/approve", headers=admin_headers
        )
        check(
            "Hospital admin can approve their hospital's claim",
            r.status_code == 200,
            f"HTTP {r.status_code} {r.text[:90]}",
        )

        sysadmin_headers = login(client, "admin@suwapath.lk")
        r = client.post(
            f"{API}/actions/{by_key['sysadmin'].id}/approve", headers=sysadmin_headers
        )
        check(
            "System admin can approve the platform claim",
            r.status_code == 200,
            f"HTTP {r.status_code} {r.text[:90]}",
        )

        section("Approval negatives")

        registry.propose(
            db,
            subject=patient,
            audience=doctor,
            action_name="schedule_reminder",
            args={"title": "Second", "body": "Another."},
            title="Second doctor proposal",
            preview_text="For the negative tests.",
            evidence={"detector_id": MARKER},
            idempotency_key=f"{MARKER}:doctor2",
        )
        second = (
            db.query(ActionProposal)
            .filter(ActionProposal.idempotency_key == f"{MARKER}:doctor2")
            .one()
        )

        for email, label in (
            ("patient@suwapath.lk", "The patient it is about"),
            ("maternal@suwapath.lk", "An unrelated patient"),
            ("hospital@suwapath.lk", "A hospital admin"),
        ):
            r = client.post(
                f"{API}/actions/{second.id}/approve", headers=login(client, email)
            )
            check(
                f"{label} cannot approve a doctor-addressed proposal",
                r.status_code == 404,
                f"HTTP {r.status_code}",
            )

        _ = treating  # kept for readability of the setup above

    finally:
        try:
            cleanup(db)
        except Exception:  # noqa: BLE001
            db.rollback()
        db.close()
        client.close()

    print("\n" + "=" * 74)
    print("RESULTS")
    print("=" * 74)
    print(f"  Passed: {len(PASSED)}")
    print(f"  Failed: {len(FAILED)}")
    for label in FAILED:
        print(f"    - {label}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
