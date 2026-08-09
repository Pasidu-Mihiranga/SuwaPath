"""Every `SomeEnum.MEMBER` in the codebase must actually exist.

Three references to enum members that were never defined shipped to main and
survived a full test run, because the code paths holding them — a T0 action and
two rungs of an escalation ladder — only execute in situations the suite never
reached. `Notification.category` is a plain `String(32)`, not a database enum,
so there is no schema-level backstop either. The failure was a plain
`AttributeError`, at the worst possible moment: inside a background job, in
production, with nobody watching.

This test needs no server, no database and no fixtures. It parses the enum
definitions, scans every `.py` file for `Class.MEMBER` references, and fails on
anything that does not resolve. It runs in milliseconds and covers all 25 enums
at once — including the ones nobody has written a behavioural test for.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENUMS_FILE = ROOT / "app" / "models" / "enums.py"
SEARCH_DIRS = (ROOT / "app", ROOT / "tests")


def enum_members() -> dict[str, set[str]]:
    """`{"AlertSeverity": {"INFO", "ATTENTION", "CRITICAL"}, ...}`"""
    tree = ast.parse(ENUMS_FILE.read_text())
    return {
        node.name: {
            target.targets[0].id
            for target in node.body
            if isinstance(target, ast.Assign)
            and isinstance(target.targets[0], ast.Name)
        }
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def bad_references() -> list[str]:
    enums = enum_members()
    # Only SCREAMING_CASE, so this never matches a method call or an attribute.
    patterns = {
        name: re.compile(rf"\b{name}\.([A-Z][A-Z_0-9]*)\b") for name in enums
    }

    problems: list[str] = []
    for directory in SEARCH_DIRS:
        for path in sorted(directory.rglob("*.py")):
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                for name, pattern in patterns.items():
                    for member in pattern.findall(line):
                        if member not in enums[name]:
                            problems.append(
                                f"{path.relative_to(ROOT)}:{lineno} "
                                f"{name}.{member} does not exist"
                            )
    return problems


def main() -> int:
    enums = enum_members()
    problems = bad_references()

    print(f"Scanned {len(enums)} enums across app/ and tests/.")
    for problem in problems:
        print(f"    FAIL  {problem}")

    if problems:
        print(f"\n  Failed: {len(problems)} undefined enum reference(s).")
        return 1
    print("  Passed: every enum reference resolves.")
    return 0


def test_no_undefined_enum_references() -> None:
    """pytest entry point, for when the suite is run that way."""
    assert bad_references() == []


if __name__ == "__main__":
    raise SystemExit(main())
