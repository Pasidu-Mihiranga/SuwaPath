"""Every ladder stage and every auto-executing action must actually run.

Three references to enum members that did not exist shipped to main and passed
a full test run, because the code holding them — one T0 action and two rungs
of the referral escalation ladder — only executes in situations no test ever
reached. The suite was green and two thirds of the ladder had never run.

Fixing those three lines was the easy part. This file is the part that stops
it recurring: it enumerates the ladder and the action registry from the code
itself, exercises each entry, and then **asserts that it covered all of them**.
Add a rung or a T0 action without a fixture here and this suite fails — which
is the point. A test that only covers what someone remembered to add is the
thing that failed the first time.

    uvicorn app.main:app --port 8000
    python tests/test_coverage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import actions as registry  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.models.agentic import ActionProposal, AgentTask  # noqa: E402
from app.models.clinical import Recommendation  # noqa: E402
from app.models.enums import (  # noqa: E402
    AlertSeverity,
    GuardianPermissionType,
    NotificationCategory,
)
from app.models.identity import User  # noqa: E402
from app.services.detectors import referrals  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []
MARKER = "coverage-suite-fixture"


def check(label: str, condition: bool, detail: str = "") -> bool:
    (PASSED if condition else FAILED).append(label)
    print(f"    {'PASS' if condition else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    return condition


def section(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


# Arguments for every T0 action. A new T0 action with no entry here fails the
# coverage assertion below rather than going quietly untested.
def t0_fixtures(patient: User) -> dict[str, dict]:
    return {
        "schedule_reminder": {
            "patient": patient,
            "title": "Coverage fixture",
            "body": "Exercising the action.",
            "category": NotificationCategory.FOLLOW_UP,
        },
        "raise_guardian_alert": {
            "patient": patient,
            "alert_type": "coverage_fixture",
            "severity": AlertSeverity.ATTENTION,
            "title": "Coverage fixture",
            "detail": "Exercising the action.",
            "permission": GuardianPermissionType.APPOINTMENTS,
            "evidence": {"detector_id": MARKER},
        },
    }


def main() -> int:
    db = SessionLocal()
    # Driving the ladder creates real proposals tagged by the *detector*, not
    # by this suite, so a marker-based cleanup misses them and the next suite
    # sees a patient over the attention cap. Snapshot instead, and remove
    # whatever appeared.
    pre_existing = {row[0] for row in db.query(ActionProposal.id).all()}
    try:
        patient = db.query(User).filter(User.email == "patient@suwapath.lk").one()
        rec = (
            db.query(Recommendation)
            .filter(
                Recommendation.patient_user_id == patient.id,
                Recommendation.is_active.is_(True),
            )
            .first()
        )
        if rec is None:
            print("No active recommendation to drive the ladder with.")
            return 1

        section("Every rung of the referral ladder executes")

        all_stages = {
            stage.name for stages in referrals.LADDER.values() for stage in stages
        }
        exercised: set[str] = set()
        original_active = rec.is_active

        for stage in sorted(all_stages):
            task = AgentTask(
                kind=referrals.TASK_KIND,
                dedupe_key=f"{MARKER}:{stage}",
                subject_user_id=patient.id,
                payload={
                    "recommendation_id": rec.id,
                    "stage": stage,
                    "urgency": "urgent",
                    "age_days": 9,
                },
            )
            try:
                referrals.handle(db, task)
                exercised.add(stage)
                check(f"stage '{stage}' ran", True)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                check(f"stage '{stage}' ran", False, f"{type(exc).__name__}: {exc}")
            finally:
                # `close` deactivates the recommendation; restore it so the
                # remaining stages still have something to act on.
                rec.is_active = original_active
                db.commit()

        check(
            "Every ladder stage is covered by this suite",
            exercised == all_stages,
            f"missing: {sorted(all_stages - exercised) or 'none'}",
        )

        section("Every T0 action executes")

        fixtures = t0_fixtures(patient)
        t0_names = {n for n, a in registry.ACTIONS.items() if a.tier == "T0"}
        covered: set[str] = set()

        for name in sorted(t0_names):
            kwargs = fixtures.get(name)
            if kwargs is None:
                check(f"T0 '{name}' has a fixture", False, "no fixture defined")
                continue
            try:
                registry.get(name).run(db, **kwargs)
                db.commit()
                covered.add(name)
                check(f"T0 '{name}' ran", True)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                check(f"T0 '{name}' ran", False, f"{type(exc).__name__}: {exc}")

        check(
            "Every T0 action is covered by this suite",
            covered == t0_names,
            f"missing: {sorted(t0_names - covered) or 'none'}",
        )

        section("Every registered action declares a valid tier")

        tiers = {a.tier for a in registry.ACTIONS.values()}
        check("All tiers are T0 or T1", tiers <= {"T0", "T1"}, str(sorted(tiers)))
        check(
            "No clinical write action is registered",
            not (
                {"prescribe", "write_consultation", "set_urgency", "grant_consent"}
                & set(registry.ACTIONS)
            ),
        )

    finally:
        try:
            db.query(AgentTask).filter(
                AgentTask.dedupe_key.like(f"{MARKER}%")
            ).delete(synchronize_session=False)
            created = [
                row[0]
                for row in db.query(ActionProposal.id).all()
                if row[0] not in pre_existing
            ]
            if created:
                db.query(ActionProposal).filter(
                    ActionProposal.id.in_(created)
                ).delete(synchronize_session=False)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        db.close()

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
