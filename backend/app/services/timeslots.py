"""Turning `"08:00"` into a real moment.

Medication schedules are stored as wall-clock strings — `["08:00", "20:00"]` —
which is the right way to store them: a patient who takes a tablet with
breakfast wants 08:00 local, not a fixed instant that drifts across a timezone
change.

Interpreting them was previously done inline with `tzinfo=timezone.utc`, which
made `"08:00"` mean 13:30 in Colombo. Nothing caught it because only one piece
of code did the interpreting and it was consistent with itself. The moment a
second reader appears — a job materialising overdue doses — the two must agree,
or every adherence number silently becomes noise.

Hence one function, used by both.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from app.services import clock


def parse_hhmm(value: str) -> time | None:
    """`"08:00"` → `time(8, 0)`. Returns None for anything unparseable.

    Schedule strings come from seeded data and from clinicians typing into a
    form, so a malformed entry must skip that dose rather than fail the job
    for every other patient.
    """
    try:
        hour, minute = (int(part) for part in value.strip().split(":", 1))
    except (ValueError, AttributeError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def at_local(day: date, value: str) -> datetime | None:
    """The instant `value` falls on `day` locally, expressed in UTC."""
    local = local_datetime(day, value)
    return local.astimezone(timezone.utc) if local else None


def local_datetime(day: date, value: str) -> datetime | None:
    """Same as `at_local` but keeps the local zone, for display."""
    parsed = parse_hhmm(value)
    if parsed is None:
        return None
    return datetime.combine(day, parsed, tzinfo=clock.local_zone())


def due_times_between(
    schedule: list[str] | None, start: datetime, end: datetime
) -> list[datetime]:
    """Every scheduled dose falling in `(start, end]`, ascending.

    Walks local calendar days rather than adding 24-hour steps, so a daylight
    change — which Sri Lanka does not observe, but a future deployment might —
    cannot shift a dose or duplicate one.
    """
    if not schedule or end <= start:
        return []

    times = [t for t in (parse_hhmm(entry) for entry in schedule) if t is not None]
    if not times:
        return []

    zone = clock.local_zone()
    day = start.astimezone(zone).date() - timedelta(days=1)
    last = end.astimezone(zone).date()

    out: list[datetime] = []
    while day <= last:
        for moment in times:
            candidate = datetime.combine(day, moment, tzinfo=zone)
            if start < candidate <= end:
                out.append(candidate.astimezone(start.tzinfo or zone))
        day += timedelta(days=1)
    return sorted(out)


def next_due(schedule: list[str] | None, *, after: datetime | None = None) -> datetime | None:
    """The next scheduled dose strictly after `after` (default: now)."""
    reference = after or clock.now()
    upcoming = due_times_between(schedule, reference, reference + timedelta(days=2))
    return upcoming[0] if upcoming else None
