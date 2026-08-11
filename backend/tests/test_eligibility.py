"""Care-programme eligibility, and the call-shape bug that shipped with it.

Two unrelated things live here because they were found together.

**Part one** checks `services/eligibility.py` decides the right thing for each
programme and profile. The rules are pure functions over a profile, so they
need no database.

**Part two** is a static scan for a bug class that pyflakes cannot see and that
I shipped in this very change: calling a function with keyword-only parameters
positionally. `resolve_patient_access(db, user, pid, PERMISSION)` imports
cleanly, type-checks nothing, and raises `TypeError` at request time — so every
guardian acting for a dependent got a 500 while every patient path stayed
green. A test that only exercised the patient route would not have caught it,
and neither did the six existing suites.

Neither part needs a server or fixtures.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import eligibility  # noqa: E402

# --------------------------------------------------------------------------
# Part one: the rules
# --------------------------------------------------------------------------


@dataclass
class FakeProgramme:
    programme_type: str

    def __str__(self) -> str:  # pragma: no cover - not used
        return self.programme_type


@dataclass
class FakeProfile:
    age: int | None = None
    sex: str | None = None
    is_pregnant: bool = False


@dataclass
class FakeMaternal:
    expected_delivery_date: object = None
    is_postpartum: bool = False


# (label, programme_type, profile, maternal, expected verdict)
CASES = [
    # A man cannot be enrolled in a pregnancy programme. The one refusal.
    ("male → maternal", "maternal", FakeProfile(69, "male"), None, "ineligible"),
    ("male → postpartum", "postpartum", FakeProfile(69, "male"), None, "ineligible"),
    # A woman with no recorded pregnancy is asked to confirm, not refused —
    # the record may simply be out of date.
    ("not pregnant → maternal", "maternal", FakeProfile(32, "female"), None, "confirm"),
    ("pregnant → maternal", "maternal", FakeProfile(28, "female", True), None, "eligible"),
    # Postpartum keys off a maternal record, not the pregnancy flag: someone
    # who has given birth is no longer pregnant.
    (
        "post-birth → postpartum",
        "postpartum",
        FakeProfile(28, "female"),
        FakeMaternal(expected_delivery_date=object()),
        "eligible",
    ),
    (
        "no maternal record → postpartum",
        "postpartum",
        FakeProfile(28, "female"),
        None,
        "confirm",
    ),
    # Age is a design target, not a cliff.
    ("69 → elderly", "elderly", FakeProfile(69, "male"), None, "eligible"),
    ("32 → elderly", "elderly", FakeProfile(32, "female"), None, "confirm"),
    # Never gated, for anyone. This pathway exists precisely for people who
    # would not otherwise ask.
    ("male → sexual health", "sexual_health", FakeProfile(69, "male"), None, "eligible"),
    ("teen → sexual health", "sexual_health", FakeProfile(17, "female"), None, "eligible"),
    # A profile with nothing filled in must not crash or hard-refuse.
    ("empty profile → maternal", "maternal", FakeProfile(), None, "confirm"),
    ("no profile → elderly", "elderly", None, None, "eligible"),
]


def rule_failures() -> list[str]:
    problems = []
    for label, ptype, profile, maternal, expected in CASES:
        result = eligibility.assess(
            FakeProgramme(ptype), profile, maternal=maternal
        )
        if result.verdict != expected:
            problems.append(f"{label}: expected {expected}, got {result.verdict}")
            continue

        # Only `ineligible` blocks. If `confirm` ever stops being allowed,
        # every unverified profile silently loses access to its programme.
        if result.allowed != (expected != "ineligible"):
            problems.append(f"{label}: verdict {expected} but allowed={result.allowed}")

        # A refusal or a confirmation the patient cannot read is a dead end.
        if expected == "ineligible" and not result.reason:
            problems.append(f"{label}: ineligible with no reason given")
        if expected == "confirm" and not result.confirmation:
            problems.append(f"{label}: confirm with nothing to confirm")

        # The copy is read both by the patient and by a guardian looking at a
        # dependent, so it must not address the reader as the subject.
        for text in (result.reason, result.confirmation):
            if "Your profile" in text:
                problems.append(f"{label}: second-person copy leaks to guardians")
    return problems


# --------------------------------------------------------------------------
# Part two: keyword-only parameters passed positionally
# --------------------------------------------------------------------------


def keyword_only_functions() -> dict[str, int]:
    """`{"resolve_patient_access": 3}` — name → how many positional params."""
    found: dict[str, int] = {}
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.args.kwonlyargs:
                continue
            allowed = len(node.args.posonlyargs) + len(node.args.args)
            # A name defined twice with different shapes cannot be checked by
            # name alone, so take the most permissive.
            found[node.name] = max(found.get(node.name, 0), allowed)
    return found


def resolvable_names(tree: ast.Module) -> set[str]:
    """Names in this file that certainly refer to a function defined in `app/`.

    Only bare-name calls are checked, and only when the name is imported from
    an `app.` module or defined in the same file. Matching on the bare name
    alone is not good enough: `session.run(...)` on an ONNX session and
    `messages.append(...)` on a list both collide with `app` functions that
    happen to share a name, and every one of those is a false alarm.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app"):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def positional_misuse() -> list[str]:
    limits = keyword_only_functions()
    problems = []
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        local = resolvable_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            if name not in limits or name not in local:
                continue
            # `*args` makes the count unknowable; skip rather than guess.
            if any(isinstance(a, ast.Starred) for a in node.args):
                continue
            if len(node.args) > limits[name]:
                rel = path.relative_to(ROOT)
                problems.append(
                    f"{rel}:{node.lineno}: {name}() takes {limits[name]} positional "
                    f"argument(s), {len(node.args)} given — the rest are "
                    f"keyword-only and this raises TypeError at runtime"
                )
    return problems


def main() -> int:
    rules = rule_failures()
    calls = positional_misuse()

    print(f"Checked {len(CASES)} eligibility cases.")
    for problem in rules:
        print(f"    FAIL  {problem}")

    limits = keyword_only_functions()
    print(f"Scanned calls to {len(limits)} function(s) with keyword-only parameters.")
    for problem in calls:
        print(f"    FAIL  {problem}")

    total = len(rules) + len(calls)
    print(f"\n  Failed: {total}")
    return 1 if total else 0


def test_eligibility_rules() -> None:
    """pytest entry point, for when the suite is run that way."""
    assert rule_failures() == []


def test_no_keyword_only_positional_calls() -> None:
    assert positional_misuse() == []


if __name__ == "__main__":
    raise SystemExit(main())
