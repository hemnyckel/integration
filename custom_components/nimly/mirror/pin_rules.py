"""Slot rules: which slots may hold user credentials and how many.

Pure logic with no Home Assistant imports (the unit tests load it by path).

The manuals reserve slots 0-2 for master credentials on every model but the
Code Pro, which reserves only slot 0 and documents 1-999 as user slots. The
reported model string cannot tell the models apart, so the reserved count is
a per-lock option, clamped so slot 0 is never written whatever is stored. The
ceiling comes from the lock's own NumberOfPINUsersSupported when it has
reported one (50 on the NimlyPRO24), otherwise the manuals' up-to-999 range.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

OPTION_RESERVED_SLOTS = "reserved_slots"

RESERVED_SLOTS_DEFAULT = 3
RESERVED_SLOTS_MIN = 1
RESERVED_SLOTS_MAX = 3

SLOT_CAPACITY_FALLBACK = 1000
SLOT_CAPACITY_SANE_MAX = 1000


def first_user_slot(options: Mapping[str, Any] | None) -> int:
    """Lowest slot a credential write may touch. Slot 0 stays protected."""
    raw = (options or {}).get(OPTION_RESERVED_SLOTS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return RESERVED_SLOTS_DEFAULT
    return max(RESERVED_SLOTS_MIN, min(RESERVED_SLOTS_MAX, value))


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


def check_credential_slot(
    slot: int,
    options: Mapping[str, Any] | None,
    facts: Mapping[str, Any] | None,
) -> str | None:
    """None when a credential may be written to the slot, else the reason."""
    if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
        return f"slot {slot} is not a valid slot number"
    floor = first_user_slot(options)
    if slot < floor:
        return (
            f"slot {slot} is reserved for master credentials "
            f"(user slots start at {floor})"
        )
    ceiling = pin_capacity(facts)
    if slot >= ceiling:
        return f"slot {slot} is outside the lock's PIN capacity (1-{ceiling - 1})"
    return None
