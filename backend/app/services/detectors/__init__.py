"""Detectors: the part of the system that notices things nobody reported.

Every module here follows the same contract, and it is the contract that makes
autonomy safe rather than alarming:

**A detector reads and enqueues. It never acts.** It does not send, book,
alert or write clinical state. It looks at the data, decides that a care
journey has stalled, and puts a task on the queue with a dedupe key describing
*what it noticed*. Acting is the handler's job, and handlers run once.

That split is why running these every few minutes is safe: a detector firing a
thousand times produces one task, because the database rejects the duplicate
key. Nothing depends on the detector being careful.
"""

from __future__ import annotations

# Importing each module is what registers its scheduled job and its task
# handler. Listed explicitly rather than discovered, so the set of things this
# system does on its own is greppable in one place.
from app.services.detectors import appointments  # noqa: F401
from app.services.detectors import checkins  # noqa: F401
from app.services.detectors import medication  # noqa: F401
from app.services.detectors import referrals  # noqa: F401

__all__ = ["appointments", "checkins", "medication", "referrals"]
