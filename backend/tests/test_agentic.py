"""Verification for the autonomy layer.

Runs against a live API and a real database, like the other suites:

    uvicorn app.main:app --port 8000
    python tests/test_agentic.py

The detectors are exercised in this process rather than through HTTP, because
the point of them is that no HTTP request is involved. The virtual clock
(`app/services/clock.py`) is what makes a 30-day escalation ladder testable in
milliseconds — every detector reads time through it.

What is being asserted, in one sentence: the system noticed a stalled care
journey by itself, prepared a specific action, did **not** perform it, and
performed it only when a human approved.
"""

from __future__ import annotations

import sys
from datetime import timedelta

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal  # noqa: E402
from app.models.agentic import ActionProposal, AgentTask  # noqa: E402
from app.models.care import Appointment  # noqa: E402
from app.models.clinical import Recommendation  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.services import clock  # noqa: E402
from app.services.detectors import referrals  # noqa: E402
from app.services.jobs import runner  # noqa: E402

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api/v1"
PASSWORD = "Demo@1234"

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        PASSED.append(label)
        print(f"    PASS  {label}" + (f" — {detail}" if detail else ""))
    else:
        FAILED.append(label)
        print(f"    FAIL  {label}" + (f" — {detail}" if detail else ""))
    return condition


def login(client: httpx.Client, email: str) -> str:
    response = client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    response.raise_for_status()
    return response.json()["access_token"]


def section(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


MARKER = "agentic-suite-fixture"


def reset_state(db, patient_id: str) -> None:
    """Remove everything a previous run created, including its fixture.

    The suite seeds its own recommendation rather than borrowing one from the
    demo data. Booking against seeded data marks other recommendations in the
    same specialty as converted, so a suite that relied on it passed once and
    then reported a false failure on the next run.
    """
    fixtures = (
        db.query(Recommendation)
        .filter(
            Recommendation.patient_user_id == patient_id,
            Recommendation.care_category == MARKER,
        )
        .all()
    )
    for rec in fixtures:
        db.query(Appointment).filter(Appointment.recommendation_id == rec.id).delete()
        db.delete(rec)

    db.query(ActionProposal).filter(ActionProposal.subject_user_id == patient_id).delete()
    db.query(AgentTask).filter(AgentTask.subject_user_id == patient_id).delete()
    db.commit()


def seed_stalled_recommendation(db, patient_id: str) -> Recommendation:
    """An urgent recommendation, made three days ago, never acted on."""
    from app.models.providers import Specialty

    specialty = (
        db.query(Specialty).filter(Specialty.code == "general_medicine").one_or_none()
        or db.query(Specialty).first()
    )
    rec = Recommendation(
        patient_user_id=patient_id,
        source="symptom",
        care_category=MARKER,
        specialty_id=specialty.id if specialty else None,
        specialty_code=specialty.code if specialty else "general_medicine",
        urgency="urgent",
        reason="Suite fixture: care advised but never arranged.",
        suggested_next_action="See a general physician within a week.",
        confidence=0.8,
        is_active=True,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def main() -> int:
    db = SessionLocal()
    client = httpx.Client(timeout=60.0)

    try:
        # Deliberately a patient with no other active recommendations. The
        # detector caps how many follow-ups one person may have open at once,
        # so a patient already carrying a backlog would have that budget spent
        # before the fixture was reached — and the suite would report a
        # failure that was actually the cap working correctly.
        patient = db.query(User).filter(User.email == "maternal@suwapath.lk").one()
        reset_state(db, patient.id)
        fixture = seed_stalled_recommendation(db, patient.id)

        section("Detection — the system notices with no request")

        # Nothing has happened yet.
        before = db.query(AgentTask).filter(AgentTask.subject_user_id == patient.id).count()
        check("No autonomous tasks before detection", before == 0, f"{before} tasks")

        check(
            "A stalled care journey exists to be found",
            fixture.is_active and fixture.care_category == MARKER,
            f"{fixture.specialty_code} / {fixture.urgency}",
        )

        # Move time forward past the urgent ladder's first rung. Nothing else
        # in the system is aware this happened.
        clock.advance(timedelta(days=3))
        queued = referrals.detect(db)
        check("Detector queued follow-up work unprompted", queued > 0, f"{queued} task(s)")

        section("Idempotency — running it again must change nothing")

        second = referrals.detect(db)
        check("Re-running the detector queues nothing new", second == 0, f"{second} task(s)")
        third = referrals.detect(db)
        check("Third run also queues nothing", third == 0, f"{third} task(s)")

        section("Handling — work becomes a proposal, not an action")

        ran = runner.drain()
        check("Queued tasks ran", ran > 0, f"{ran} task(s)")

        proposals = (
            db.query(ActionProposal)
            .filter(
                ActionProposal.subject_user_id == patient.id,
                ActionProposal.status == "pending",
            )
            .all()
        )
        check("A booking was proposed", len(proposals) > 0, f"{len(proposals)} proposal(s)")

        if not proposals:
            return 1

        proposal = next(
            (
                p
                for p in proposals
                if p.action_name == "book_appointment"
                and p.args.get("recommendation_id") == fixture.id
            ),
            None,
        )
        check("Proposal is a concrete booking", proposal is not None)
        if proposal is None:
            return 1

        check("Proposal is T1, requiring approval", proposal.risk_tier == "T1", proposal.risk_tier)
        check(
            "Proposal names a doctor and a real slot",
            bool(proposal.args.get("doctor_id")) and bool(proposal.args.get("scheduled_start")),
            proposal.args.get("scheduled_start", ""),
        )
        check(
            "Proposal carries evidence for why it exists",
            bool(proposal.evidence.get("detector_id")),
            str(proposal.evidence.get("detector_id")),
        )
        check(
            "Preview explains the choice to the patient",
            len(proposal.preview_text or "") > 40,
            (proposal.preview_text or "").split("\n")[0],
        )

        # The critical negative: nothing was booked.
        booked = (
            db.query(Appointment)
            .filter(Appointment.recommendation_id == proposal.args.get("recommendation_id"))
            .count()
        )
        check("Nothing was booked without approval", booked == 0, f"{booked} appointment(s)")

        section("Alert fatigue — a backlog does not become an inbox flood")

        # A patient with a long history has dozens of active recommendations.
        # Chasing all of them at once is the failure mode that makes proactive
        # health software get muted, so the detector caps how many follow-ups
        # one person may have open.
        busy = db.query(User).filter(User.email == "patient@suwapath.lk").one()
        open_for_busy = (
            db.query(ActionProposal)
            .filter(
                ActionProposal.subject_user_id == busy.id,
                ActionProposal.status == "pending",
            )
            .count()
        )
        check(
            "A patient with a large backlog is capped",
            open_for_busy <= referrals.MAX_OPEN_PER_PATIENT,
            f"{open_for_busy} open (cap {referrals.MAX_OPEN_PER_PATIENT})",
        )

        section("Approval — one tap, and the shared booking path runs")

        token = login(client, "maternal@suwapath.lk")
        headers = {"Authorization": f"Bearer {token}"}

        listed = client.get(f"{API}/actions", headers=headers)
        check("Patient can see their pending suggestions", listed.status_code == 200)
        check(
            "The proposal appears in the inbox",
            any(p["id"] == proposal.id for p in listed.json().get("proposals", [])),
        )

        approved = client.post(f"{API}/actions/{proposal.id}/approve", headers=headers)
        ok = check(
            "Approval executed the booking",
            approved.status_code == 200,
            approved.text[:120],
        )

        if ok:
            db.expire_all()
            after = (
                db.query(Appointment)
                .filter(
                    Appointment.recommendation_id == proposal.args.get("recommendation_id")
                )
                .count()
            )
            check("An appointment now exists", after == 1, f"{after} appointment(s)")

            refreshed = db.get(ActionProposal, proposal.id)
            check("Proposal is marked executed", refreshed.status == "executed", refreshed.status)

            replay = client.post(f"{API}/actions/{proposal.id}/approve", headers=headers)
            check(
                "Approving twice is rejected",
                replay.status_code == 409,
                f"HTTP {replay.status_code}",
            )

        section("Authorisation — a stranger cannot approve or even see it")

        other_token = login(client, "patient@suwapath.lk")
        other_headers = {"Authorization": f"Bearer {other_token}"}
        remaining = (
            db.query(ActionProposal)
            .filter(
                ActionProposal.subject_user_id == patient.id,
                ActionProposal.status == "pending",
            )
            .first()
        )
        if remaining:
            stranger = client.post(
                f"{API}/actions/{remaining.id}/approve", headers=other_headers
            )
            check(
                "Another patient cannot approve it",
                stranger.status_code == 404,
                f"HTTP {stranger.status_code}",
            )
            check(
                "Rejection does not confirm the proposal exists",
                stranger.status_code == 404,
                "404 rather than 403",
            )

        section("Safety — the ladder cannot be skipped and T2 does not exist")

        from app.agent import actions as registry

        check(
            "No clinical write action is registered",
            not any(
                name in registry.ACTIONS
                for name in ("prescribe", "write_consultation", "set_urgency", "grant_consent")
            ),
        )
        tiers = {a.tier for a in registry.ACTIONS.values()}
        check("Every registered action is T0 or T1", tiers <= {"T0", "T1"}, str(tiers))

        try:
            registry.get("raise_guardian_alert").run(
                db,
                patient=patient,
                alert_type="test",
                severity=None,
                title="t",
                detail="d",
                permission=None,
                evidence={},
            )
            check("Alert without evidence is refused", False, "it ran")
        except registry.ActionError:
            check("Alert without evidence is refused", True)
        except Exception as exc:  # noqa: BLE001
            check("Alert without evidence is refused", False, type(exc).__name__)

    finally:
        clock.reset()
        # Leave the database as it was found.
        try:
            reset_state(db, patient.id)
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
