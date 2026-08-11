"""Forward-only schema changes that `create_all()` cannot make.

`create_all()` creates missing *tables*. It never adds a column to a table
that already exists, never drops a constraint, and never builds an index on
something it did not just create. So every schema change after the first
deployment is invisible to it.

The honest options were Alembic or this. Alembic means baselining 35 existing
tables and carrying a migration environment for a project that deploys as a
single container; this is a list of idempotent statements applied in order,
and it costs half an hour. If this project ever grows a second deployment or a
rollback requirement, replace it with Alembic rather than extending it.

It also carries **data** corrections, for the same reason: the seeder writes
its rows only on a fresh `--reset`, so a fix to seeded content never reaches a
deployment that is already running. Those statements are keyed on a stable
business key (a programme `code`, not an id) so re-running rewrites the same
row rather than creating a second one.

Every statement here must be safe to run twice — Postgres gives us
`IF NOT EXISTS` for columns and indexes, and dropping a NOT NULL that is
already dropped is a no-op.

**Nothing runs this automatically.** It is not in the Dockerfile, the compose
file or the app's startup, so a deploy that needs it needs the command below
run by hand afterwards.

    python -m scripts.migrate
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.db import engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
logger = logging.getLogger("migrate")

# (description, SQL). Append only — never edit or reorder a shipped entry.
MIGRATIONS: list[tuple[str, str]] = [
    (
        "action_proposals.audience_user_id",
        """
        ALTER TABLE action_proposals
        ADD COLUMN IF NOT EXISTS audience_user_id VARCHAR(36)
        REFERENCES users(id) ON DELETE CASCADE
        """,
    ),
    (
        "action_proposals.audience_role",
        "ALTER TABLE action_proposals ADD COLUMN IF NOT EXISTS audience_role VARCHAR(16)",
    ),
    (
        "action_proposals.audience_scope_id",
        "ALTER TABLE action_proposals ADD COLUMN IF NOT EXISTS audience_scope_id VARCHAR(36)",
    ),
    (
        "action_proposals.features",
        "ALTER TABLE action_proposals ADD COLUMN IF NOT EXISTS features JSONB DEFAULT '{}'::jsonb",
    ),
    (
        "action_proposals.subject_user_id becomes nullable",
        "ALTER TABLE action_proposals ALTER COLUMN subject_user_id DROP NOT NULL",
    ),
    (
        "appointments.status_source",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS status_source "
        "VARCHAR(16) DEFAULT 'human'",
    ),
    (
        "index: proposals by audience",
        """
        CREATE INDEX IF NOT EXISTS ix_action_proposals_audience
        ON action_proposals (audience_user_id, status)
        """,
    ),
    (
        "index: proposals by role claim",
        """
        CREATE INDEX IF NOT EXISTS ix_action_proposals_role
        ON action_proposals (audience_role, audience_scope_id, status)
        """,
    ),
    # Data, not schema. The seeder only writes these rows on a fresh `--reset`,
    # so a fix to a programme's name never reaches a deployment that is already
    # running — the old name sits in the database indefinitely. Keyed on `code`,
    # which is the stable identifier; re-running just rewrites the same text.
    (
        "rename: maternal programme no longer says 'Postpartum'",
        """
        UPDATE care_programmes
        SET name = 'Pregnancy & Antenatal Care',
            description = 'Pregnancy monitoring, antenatal visit reminders, '
                          'scan and test milestones, and warning-sign check-ins.'
        WHERE code = 'maternal_care'
        """,
    ),
    (
        "rename: postpartum programme reads plainly",
        """
        UPDATE care_programmes
        SET name = 'After Birth & Newborn Care',
            description = 'Recovery after delivery, feeding support, '
                          'mental-wellbeing screening, and newborn checks and '
                          'vaccination reminders.'
        WHERE code = 'postpartum_care'
        """,
    ),
]


def run() -> int:
    applied = 0
    with engine.begin() as connection:
        for description, statement in MIGRATIONS:
            try:
                connection.execute(text(statement))
                logger.info("ok    %s", description)
                applied += 1
            except Exception as exc:  # noqa: BLE001
                # A failure here is worth seeing in full: a half-migrated
                # schema is harder to diagnose than a loud stop.
                logger.error("FAIL  %s — %s", description, exc)
                raise
    logger.info("%d statement(s) applied.", applied)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
