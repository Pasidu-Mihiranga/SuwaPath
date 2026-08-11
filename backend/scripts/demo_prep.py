"""Make the autonomy layer visible before a demo or a recording.

The detectors and the task runner are two separate stages, and only the first
one is obvious. `detect()` notices something and *enqueues* a task; a worker
claims that task later and turns it into the proposal a human actually sees.
Between those two stages the work is real, recorded in `agent_tasks`, and
completely invisible in the UI.

That gap is why a freshly started instance shows an empty Actions panel for
every role except the patient. On a database used for development it had 379
tasks sitting in `queued` — fourteen no-show reminder batches, sixty-four
lapsed follow-ups, three hundred medication checks — none of which had become
a proposal. Demoing that instance would have shown a system that notices
nothing, which is the opposite of true.

So this runs both stages, in order, and prints what each role ends up able to
see. Run it before recording anything:

    python -m scripts.demo_prep

It is safe to re-run. Detectors dedupe by intent, so a second run adds nothing
rather than doubling every proposal.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.agentic import ActionProposal  # noqa: E402
from app.api.v1.actions import _visible_filter  # noqa: E402
from app.services import jobs  # noqa: E402
from app.services.jobs import runner  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")

# A drain processes one batch; a backlog needs several. Bounded so a handler
# that re-enqueues its own work cannot spin here forever.
MAX_DRAIN_ROUNDS = 50

# The accounts a demo actually signs into.
DEMO_ACCOUNTS = (
    "patient@suwapath.lk",
    "maternal@suwapath.lk",
    "elderly@suwapath.lk",
    "guardian@suwapath.lk",
    "doctor@suwapath.lk",
    "hospital@suwapath.lk",
    "admin@suwapath.lk",
)


def main() -> int:
    jobs.load_jobs()

    print("Running detectors")
    for job in jobs.JOBS:
        try:
            jobs.run_job(job)
            print(f"  ok    {job.name}")
        except Exception as exc:  # noqa: BLE001 - one detector must not stop the rest
            print(f"  FAIL  {job.name}: {type(exc).__name__}: {exc}")

    print("\nDraining the task queue")
    total = 0
    for _ in range(MAX_DRAIN_ROUNDS):
        done = runner.drain()
        if not done:
            break
        total += done
    print(f"  {total} task(s) processed")

    # Counted per demo account, not per role. A role-level total is misleading
    # here: there are thousands of seeded patients, so "41 pending" says
    # nothing about whether the account you are about to sign into on camera
    # has anything on screen.
    print("\nWhat each demo account will see in its Actions panel")
    empty = []
    with SessionLocal() as db:
        for email in DEMO_ACCOUNTS:
            user = db.execute(
                select(User).where(User.email == email)
            ).scalar_one_or_none()
            if user is None:
                print(f"  {email:24s} — no such account (is the database seeded?)")
                empty.append(email)
                continue

            # The API's own visibility clause, imported rather than
            # reimplemented. A proposal reaches someone through one of three
            # routes — addressed to them, about them, or claimable by their
            # role and scope — and a second copy of that logic here would
            # drift and quietly report the wrong thing before a recording.
            rows = db.execute(
                select(ActionProposal.action_name, func.count())
                .where(
                    ActionProposal.status == "pending",
                    _visible_filter(db, user),
                )
                .group_by(ActionProposal.action_name)
            ).all()

            if not rows:
                print(f"  {email:24s} nothing pending")
                empty.append(email)
                continue
            summary = ", ".join(f"{action} ×{n}" for action, n in rows)
            print(f"  {email:24s} {summary}")

    if empty:
        print(
            "\nAccounts with nothing pending are not necessarily broken — a "
            "detector\nonly fires when its condition holds. Just do not build "
            "a demo beat around them."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
