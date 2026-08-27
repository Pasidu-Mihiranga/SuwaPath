"""Widen the columns that now hold ciphertext, and start the audit chain.

    python scripts/encrypt_phi_columns.py          # show what would change
    python scripts/encrypt_phi_columns.py --apply

There is no Alembic in this project, so this is the migration. It is written
to be safe to run more than once and to preserve existing rows rather than
requiring a re-seed.

Two kinds of change:

**Column types.** AES-GCM ciphertext is a long base64 string, so a `JSONB`
column rejects it outright and a `VARCHAR(8)` overflows on the first write.
Both become `TEXT`. Converting `JSONB` to `TEXT` leaves the existing value as
its JSON representation, which `EncryptedJSON` reads back through its legacy
plaintext path — so records written before encryption stay readable and no
backfill is required.

**The audit chain.** Adds the two hash columns and the sequencer row. Existing
audit entries keep no hash and are reported as unverifiable rather than
invalid; the chain starts from the next entry written.

Existing rows are *not* re-encrypted. They stay plaintext and readable, and
are encrypted when next written. Backfilling would mean decrypting and
rewriting every row in one transaction, which is the riskiest possible way to
deploy this and buys nothing for synthetic data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.db import engine  # noqa: E402

# table -> columns that must become TEXT
WIDEN: dict[str, tuple[str, ...]] = {
    "patient_profiles": (
        "blood_group",
        "address",
        "chronic_conditions",
        "allergies",
        "current_medications",
        "past_surgeries",
        "family_history",
        "emergency_contact_name",
        "emergency_contact_phone",
    ),
}

AUDIT_STATEMENTS = (
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS prev_hash VARCHAR(64)",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS entry_hash VARCHAR(64)",
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_entry_hash ON audit_logs (entry_hash)",
)


def current_types(connection, table: str, columns: tuple[str, ...]) -> dict[str, str]:
    rows = connection.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = ANY(:c)"
        ),
        {"t": table, "c": list(columns)},
    ).all()
    return {name: kind for name, kind in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="run the changes")
    args = parser.parse_args()

    planned: list[str] = []
    with engine.connect() as connection:
        for table, columns in WIDEN.items():
            types = current_types(connection, table, columns)
            for column in columns:
                kind = types.get(column)
                if kind is None:
                    print(f"  ?  {table}.{column} not found — skipping")
                    continue
                if kind == "text":
                    continue
                planned.append(
                    f"ALTER TABLE {table} ALTER COLUMN {column} "
                    f"TYPE TEXT USING {column}::text"
                )
                print(f"  ~  {table}.{column}: {kind} -> text")

        audit_columns = current_types(connection, "audit_logs", ("prev_hash", "entry_hash"))
        if len(audit_columns) < 2:
            planned.extend(AUDIT_STATEMENTS)
            print("  +  audit_logs: prev_hash, entry_hash (+ index)")

    if not planned:
        print("Nothing to do — schema already current.")
        return 0

    if not args.apply:
        print(f"\n{len(planned)} statement(s). Re-run with --apply to execute.")
        return 0

    with engine.begin() as connection:
        for statement in planned:
            connection.execute(text(statement))

    # Creates audit_chain_head if it does not exist yet.
    from app.core.db import create_all

    create_all()
    print(f"\nApplied {len(planned)} statement(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
