"""Screen a set of candidate demo sentences and report what each one fires.

Run before a demo to confirm the sentences you plan to say out loud actually
reach the rule you expect. The listener is only as good as the phrasing, and
spoken phrasing differs from typed phrasing enough that guessing is unwise.

    python scripts/check_demo_prompts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.enums import UrgencyLevel  # noqa: E402
from app.services.emergency import screen  # noqa: E402
from app.services.red_flag_engine import build_context  # noqa: E402

# (sentence, patient context, expect_emergency)
CANDIDATES: list[tuple[str, dict, bool]] = [
    # --- Cardiac ---
    ("I have chest pain and I cannot breathe", {}, True),
    ("I have chest pain and I am sweating a lot", {}, True),
    ("My chest is hurting and the pain is going down my left arm", {}, True),
    ("My husband has collapsed and he is not responding", {}, True),
    # --- Stroke ---
    ("His face is drooping and he cannot speak properly", {}, True),
    ("My mother's speech is slurred and her arm has gone weak", {}, True),
    # --- Seizure / neuro ---
    ("My son is having a fit right now", {}, True),
    ("I have the worst headache of my life and I keep vomiting", {}, True),
    ("She has a high fever, a stiff neck and a terrible headache", {}, True),
    # --- Bleeding ---
    ("There is a lot of blood and the bleeding will not stop", {}, True),
    ("I am vomiting blood", {}, True),
    # --- Breathing ---
    ("She cannot breathe and her face is swelling up", {}, True),
    # --- Maternal ---
    ("I am bleeding down there", {"is_pregnant": True}, True),
    ("I have not felt the baby move today", {"is_pregnant": True}, True),
    ("I am bleeding heavily since the delivery", {"is_postpartum": True}, True),
    # --- Paediatric ---
    ("My baby is not feeding and is very sleepy", {"age": 1}, True),
    # --- Mental health ---
    ("I want to end my life", {}, True),
    # --- Sinhala / Tamil ---
    ("මට පපුවේ කැක්කුම සහ හුස්ම ගැනීමේ අපහසුතාව", {}, True),
    ("papuwe kakkuma and husma ganna amaruyi", {}, True),
    ("maarbu vali and moochu thinaral", {}, True),
    # --- Must stay quiet ---
    ("I have a mild headache since yesterday", {}, False),
    ("I have no chest pain, just a sore throat", {}, False),
    ("There is no more bleeding now", {}, False),
    ("I need to book an appointment with a dermatologist", {}, False),
    ("I cut my finger while cooking, it is a small cut", {}, False),
    ("What time is my appointment on Thursday", {}, False),
]


def main() -> int:
    wrong = 0
    for text, context, expect in CANDIDATES:
        result = screen(text, build_context(**context))
        fired = result.urgency == UrgencyLevel.EMERGENCY
        rules = ", ".join(r.rule_id for r in result.triggered_rules) or "—"
        ok = fired == expect
        wrong += not ok
        flag = "    " if ok else "BAD "
        want = "EMERGENCY" if expect else "quiet    "
        got = "EMERGENCY" if fired else "quiet    "
        ctx = f" [{context}]" if context else ""
        print(f"{flag}want={want} got={got}  {rules:<28} {text}{ctx}")

    print(f"\n{len(CANDIDATES) - wrong}/{len(CANDIDATES)} as expected")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
