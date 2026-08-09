"""The one place the application asks what time it is.

Autonomous work is defined almost entirely in terms of elapsed time: a
referral that has gone unconverted for 48 hours, a dose two hours overdue, a
check-in nobody has filed for three days. None of that can be tested against a
real clock — verifying a 30-day escalation ladder would take 30 days.

So every detector and every job reads the time from here, and a test can move
the clock forward. This is worth the indirection precisely because retrofitting
it later means auditing every `datetime.now()` in the codebase and hoping none
was missed.

Request handlers may keep calling `datetime.now(timezone.utc)` directly; they
run in real time by definition. The rule applies to anything that can be
replayed.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings

# Set only by tests. A module-level offset rather than a patched function so
# that code holding a reference to `now` still sees the shift.
_offset = timedelta(0)
_lock = threading.Lock()


def local_zone() -> ZoneInfo:
    return ZoneInfo(settings.local_timezone)


def now() -> datetime:
    """Current UTC time, plus any offset a test has applied."""
    return datetime.now(timezone.utc) + _offset


def local_now() -> datetime:
    """Current time where the patient is, which is where 08:00 means 08:00."""
    return now().astimezone(local_zone())


def today() -> date:
    """The local date. Deliberately not the UTC date.

    A job bucketing work by day must agree with the patient's calendar: at
    02:00 in Colombo the UTC date is still yesterday, and a dedupe key built
    from it would let a daily job run twice.
    """
    return local_now().date()


# --------------------------------------------------------------------------
# Test control
# --------------------------------------------------------------------------
def advance(delta: timedelta) -> None:
    """Move the clock forward. Tests only."""
    global _offset
    with _lock:
        _offset += delta


def reset() -> None:
    global _offset
    with _lock:
        _offset = timedelta(0)


def current_offset() -> timedelta:
    return _offset


@contextmanager
def frozen_at(moment: datetime):
    """Run a block as though it were `moment`."""
    global _offset
    previous = _offset
    with _lock:
        _offset = moment - datetime.now(timezone.utc)
    try:
        yield
    finally:
        with _lock:
            _offset = previous
