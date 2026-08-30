"""Ensure the demo doctor has patients in today's live queue.

Run this when the Patient Queue or dashboard looks empty — most often on a
Sunday, before the seeder included Sunday clinic hours for the demo doctor.

    python -m scripts.ensure_demo_doctor_queue
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal  # noqa: E402
from app.seed.journeys import ensure_demo_doctor_today_queue  # noqa: E402


def main() -> int:
    with SessionLocal() as db:
        created = ensure_demo_doctor_today_queue(db)
    if created:
        print(f"Added {created} patient(s) to Dr. Dileepa Perera's queue for today.")
    else:
        print("Demo doctor queue already has patients for today — nothing to add.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
