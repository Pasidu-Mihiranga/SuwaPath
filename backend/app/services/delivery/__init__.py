"""Reaching a person, on the channel their situation warrants.

`deliver()` is the entry point. Channels are additive: adding one means adding
a module here and a row to the table in `router.py`, not touching any caller.
"""

from app.services.delivery.base import DeliveryResult, Message
from app.services.delivery.router import deliver, in_quiet_hours

__all__ = ["DeliveryResult", "Message", "deliver", "in_quiet_hours"]
