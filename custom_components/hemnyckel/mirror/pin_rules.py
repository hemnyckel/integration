"""Slot rules: which slots may hold user credentials and how many.

Pure logic with no Home Assistant imports (the unit tests load it by path).

Slots 0-2 hold the locks' master credentials and are never written: not by a
service, not by a guest code, not by a fingerprint enrollment. The floor is a
constant rather than a setting, because a master slot is not something a stored
option should be able to talk anyone into writing to. The ceiling comes from the
lock's own NumberOfPINUsersSupported when it has reported one (50 on the
NimlyPRO24), otherwise the manuals' up-to-999 range.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Slots 0, 1 and 2 are the master slots on the locks this integration serves.
# No credential write, clear or enrollment may touch them.
FIRST_USER_SLOT = 3

SLOT_CAPACITY_FALLBACK = 1000
SLOT_CAPACITY_SANE_MAX = 1000


def pin_capacity(facts: Mapping[str, Any] | None) -> int:
    """The exclusive upper bound for PIN slots, from the lock's own report."""
    raw = (facts or {}).get("num_of_pin_users_supported")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return SLOT_CAPACITY_FALLBACK
    if not 0 < value <= SLOT_CAPACITY_SANE_MAX:
        return SLOT_CAPACITY_FALLBACK
    return value


def check_credential_slot(slot: int, facts: Mapping[str, Any] | None) -> str | None:
    """None when a credential may be written to the slot, else the reason."""
    if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
        return f"slot {slot} is not a valid slot number"
    if slot < FIRST_USER_SLOT:
        return (
            f"slot {slot} is reserved for master credentials "
            f"(user slots start at {FIRST_USER_SLOT})"
        )
    ceiling = pin_capacity(facts)
    if slot >= ceiling:
        return f"slot {slot} is outside the lock's PIN capacity (1-{ceiling - 1})"
    return None
