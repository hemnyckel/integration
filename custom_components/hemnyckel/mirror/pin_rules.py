"""Slot rules: which slots may hold user credentials and how many.

Pure logic with no Home Assistant imports (the unit tests load it by path).

Slots 0-2 hold the locks' master credentials and are never written: not by a
service, not by a guest code, not by a fingerprint enrollment. The floor is a
constant rather than a setting, because a master slot is not something a stored
option should be able to talk anyone into writing to.

The ceiling is the lock family's manual range, 003-999, which the hardware was
measured to enforce. The standard NumberOfPINUsersSupported attribute is only a
hint: the NimlyPRO24 reports 50 there, yet on 2026-09-28 it answered SetPINCode
with Status.SUCCESS for slots 60, 150, 500 and 999 and with Status.FAILURE for
1000 and 65535. A report below the manual range must therefore never cap the
household; a report above it is honoured, for a model whose user space really
is larger.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Slots 0, 1 and 2 are the master slots on the locks this integration serves.
# No credential write, clear or enrollment may touch them.
FIRST_USER_SLOT = 3

# The exclusive upper bound for user credential slots: the manuals' 003-999
# range, measured as the enforced one (999 accepted, 1000 refused). The
# standard capacity attribute is a hint and can only raise this, never lower it.
SLOT_CAPACITY_CEILING = 1000

# A ZCL user_id is a uint16; anything larger cannot name a real slot.
SLOT_CAPACITY_SANE_MAX = 65535


def pin_capacity(facts: Mapping[str, Any] | None) -> int:
    """The exclusive upper bound for user credential slots.

    The lock's NumberOfPINUsersSupported attribute is a hint, not a wall: see
    the module docstring for the measurement. A reported value raises the
    ceiling only when it is above the manual range, so a value the lock does
    not enforce (50 on the NimlyPRO24) cannot cut the household short.
    """
    raw = (facts or {}).get("num_of_pin_users_supported")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return SLOT_CAPACITY_CEILING
    if not 0 < value <= SLOT_CAPACITY_SANE_MAX:
        return SLOT_CAPACITY_CEILING
    return max(value, SLOT_CAPACITY_CEILING)


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
        return f"slot {slot} is outside the lock's user slot range (1-{ceiling - 1})"
    return None
