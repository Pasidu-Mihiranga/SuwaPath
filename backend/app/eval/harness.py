"""Run the arms over the gold set and score them.

    python -m app.eval.harness --self-check
    python -m app.eval.harness --arms A,A-full            # instant, no LLM
    python -m app.eval.harness --arms B,C --runs 3        # slow, uses the free tier

Checkpointing is not a convenience here, it is a requirement. One Arm C pass is
roughly eight LLM calls per vignette — about 840 for the full set — and the free
tiers this project runs on rate-limit well below that. A run *will* be
interrupted. Results are therefore appended to a JSON checkpoint as they land
and re-running skips whatever is already recorded, so the set can be completed
across several sittings. `--fresh` discards it and starts over.

No pytest: `backend/tests/` has no pytest infrastructure and every existing
test is a standalone script, so this follows the same convention.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app.eval import arms as arms_module
from app.eval import report as report_module
from app.eval.vignettes import VIGNETTES, BY_ID, Vignette, stats
from app.services import llm
from app.services import red_flag_engine as rfe

CHECKPOINT = Path(__file__).resolve().parent / "results.json"


# --------------------------------------------------------------------------
# Gold-set self-check
# --------------------------------------------------------------------------
def self_check() -> int:
    """Compare the hand-written labels against the deterministic engine.

    Every disagreement is a finding — either the label is wrong or the engine
    is. Ones already understood carry a `known_engine_gap` and are listed
    separately, so a genuinely new disagreement cannot hide among them.
    """
    documented: list[tuple[Vignette, str, list[str]]] = []
    undocumented: list[tuple[Vignette, str, list[str]]] = []

    for vignette in VIGNETTES:
        text = " ".join([vignette.opening, *vignette.followups.values()])
        result = rfe.assess_text(text, arms_module._context(vignette))
        got_urgency = str(result.urgency)
        got_rules = sorted(rule.rule_id for rule in result.triggered_rules)
        if got_urgency == vignette.expected_urgency and got_rules == sorted(vignette.expected_rules):
            continue
        bucket = documented if vignette.known_engine_gap else undocumented
        bucket.append((vignette, got_urgency, got_rules))

    composition = stats()
    print("Gold set")
    print(f"  {composition['total']} vignettes  "
          f"({composition['emergencies']} emergencies)")
    for name, count in sorted(composition["by_category"].items()):
        print(f"    {name:14} {count}")
    print(f"  languages: {composition['by_language']}")
    print()

    if documented:
        print(f"Known engine gaps ({len(documented)}) — measured, not yet fixed")
        for vignette, got_urgency, got_rules in documented:
            print(f"  {vignette.id}: expected {vignette.expected_urgency}, got {got_urgency}")
            print(f"      {vignette.known_engine_gap}")
        print()

    if undocumented:
        print(f"UNEXPLAINED disagreements ({len(undocumented)}) — investigate")
        for vignette, got_urgency, got_rules in undocumented:
            print(f"  {vignette.id} [{vignette.category}]  {vignette.opening!r}")
            print(f"      urgency: expected {vignette.expected_urgency}, got {got_urgency}")
            print(f"      rules:   expected {sorted(vignette.expected_rules)}, got {got_rules}")
        return 1

    print("No unexplained disagreements.")
    return 0


# --------------------------------------------------------------------------
# Checkpointing
# --------------------------------------------------------------------------
def _load_checkpoint() -> list[dict]:
    if not CHECKPOINT.exists():
        return []
    try:
        return json.loads(CHECKPOINT.read_text())
    except (json.JSONDecodeError, OSError):
        print(f"  (checkpoint at {CHECKPOINT} unreadable, starting fresh)")
        return []


def _save_checkpoint(rows: list[dict]) -> None:
    CHECKPOINT.write_text(json.dumps(rows, indent=2))


def _key(row: dict) -> tuple:
    return (row["arm"], row["vignette_id"], row.get("run", 0))


# Every provider rate-limited or cooling down. Not a wrong answer — an
# unreachable one, and the two must never be scored as the same thing.
UNREACHABLE = "no provider available"
MAX_PROVIDER_RETRIES = 4


def _evaluate(arm: str, vignette: Vignette) -> dict:
    """Run one arm on one vignette, waiting out free-tier exhaustion.

    The first version of this scored a rate-limited call as a wrong answer.
    Because the vignette list ends with the negation and multilingual cases,
    a burst of 429s landed entirely on those two categories and produced a
    tidy, completely false "the LLM scores 0% on Sinhala". Provider
    exhaustion is a property of the free tier, not of the model, so it is
    waited out and retried, and only reported as unreachable if it persists.
    """
    for attempt in range(MAX_PROVIDER_RETRIES):
        try:
            row = arms_module.ARMS[arm](vignette).to_dict()
        except Exception as exc:  # noqa: BLE001 — one bad vignette must not lose the run
            return {
                "vignette_id": vignette.id, "arm": arm, "urgency": None,
                "specialty": None, "rules": [], "questions_asked": 0,
                "latency_ms": 0, "source": "", "error": f"{type(exc).__name__}: {exc}",
            }

        if row.get("error") != UNREACHABLE:
            return row

        # llm.py cools a failed provider for 60s. Sleep just past the shortest
        # remaining cooldown rather than a blind fixed delay.
        cooldowns = llm.status().get("cooling_down") or {}
        wait = min(cooldowns.values()) + 2 if cooldowns else 15
        if attempt < MAX_PROVIDER_RETRIES - 1:
            print(f"      all providers cooling down, waiting {wait:.0f}s "
                  f"(attempt {attempt + 1}/{MAX_PROVIDER_RETRIES})", flush=True)
            time.sleep(wait)

    return row


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
def run(
    arm_names: list[str], *, runs: int, limit: int | None, fresh: bool,
    retry_errors: bool = False,
) -> list[dict]:
    vignettes = VIGNETTES[:limit] if limit else VIGNETTES
    rows = [] if fresh else _load_checkpoint()

    if retry_errors:
        before = len(rows)
        rows = [row for row in rows if not row.get("error")]
        print(f"Dropped {before - len(rows)} errored row(s) for retry.")

    done = {_key(row) for row in rows}

    planned = [
        (arm, vignette, run_index)
        for arm in arm_names
        for run_index in range(runs)
        for vignette in vignettes
    ]
    todo = [item for item in planned if (item[0], item[1].id, item[2]) not in done]

    print(f"{len(planned)} evaluations planned, {len(planned) - len(todo)} already "
          f"in the checkpoint, {len(todo)} to run.")
    if not todo:
        return rows

    started = time.perf_counter()
    for index, (arm, vignette, run_index) in enumerate(todo, start=1):
        row = _evaluate(arm, vignette)
        row["run"] = run_index
        rows.append(row)

        # Save every time: a 429 or a Ctrl-C in the middle of an 840-call run
        # must not cost the work already done.
        _save_checkpoint(rows)

        elapsed = time.perf_counter() - started
        rate = elapsed / index
        remaining = int(rate * (len(todo) - index))
        marker = "!" if row.get("error") else "."
        print(f"  {marker} [{index}/{len(todo)}] {arm:7} {vignette.id:22} "
              f"{str(row['urgency']):10} ~{remaining}s left", flush=True)

    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arms", default="A,A-full",
        help="comma-separated: A, A-full, B, C (default: the two instant ones)",
    )
    parser.add_argument("--runs", type=int, default=1,
                        help="repeats per vignette, to measure variance")
    parser.add_argument("--limit", type=int, default=None,
                        help="only the first N vignettes, for a smoke test")
    parser.add_argument("--fresh", action="store_true",
                        help="discard the checkpoint and start over")
    parser.add_argument("--retry-errors", action="store_true",
                        help="re-run any checkpointed row that errored")
    parser.add_argument("--self-check", action="store_true",
                        help="validate the gold set labels and exit")
    parser.add_argument("--report-only", action="store_true",
                        help="re-render the report from the checkpoint")
    parser.add_argument("--out", default=None, help="write the report to a file")
    args = parser.parse_args()

    if args.self_check:
        return self_check()

    if args.report_only:
        rows = _load_checkpoint()
        if not rows:
            print("Checkpoint is empty — nothing to report.")
            return 1
    else:
        arm_names = [name.strip() for name in args.arms.split(",") if name.strip()]
        unknown = [name for name in arm_names if name not in arms_module.ARMS]
        if unknown:
            print(f"Unknown arm(s): {unknown}. Choose from {list(arms_module.ARMS)}.")
            return 2
        rows = run(arm_names, runs=args.runs, limit=args.limit, fresh=args.fresh,
                   retry_errors=args.retry_errors)

    text = report_module.render(rows)
    print()
    print(text)
    if args.out:
        Path(args.out).write_text(text)
        print(f"\nWritten to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
