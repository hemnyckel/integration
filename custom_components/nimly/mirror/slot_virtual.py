"""App slot virtualization: the vendor app's slot numbers, kept working.

Pure logic with no Home Assistant imports (the unit tests load it by path).

The bridge provisions credentials by slot number ("SetPINCode slot 3"). The
app stores only its own view of which slots it uses, so a naive write can land
on a slot that already holds a local credential, and a local write can land on
a slot the app believes is used. Neither is allowed to happen silently:

- A write for a virtual slot that is free on the lock passes straight through.
- A write that would collide with a local credential is relocated to a free
  real slot and remembered as virtual -> real.
- The same mapping translates the lock's own usage events back to the virtual
  number, so the vendor cloud attributes the person it provisioned.
- A clear only touches the app's own relocated credential; a local credential
  in the same-numbered slot is left alone.
- When nothing can be done safely, the resolver reports it so the coordinator
  can journal it and raise a repair instead of diverging invisibly.

Local PINs live in the high range by convention, the app's in the low, so
relocations are rare and the map stays small. Mappings are stored as string
keys because config-entry options survive a JSON round trip.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

OPTION_SLOT_MAP = "slot_map"

# Resolver outcomes.
PASS = "pass"  # write as-is; the slot is free
REUSE = "reuse"  # the virtual slot already maps to a real slot; write there
MOVE = "move"  # relocate to a real slot and remember virtual -> real
BLOCKED = "blocked"  # refused; the caller journals it and raises a repair

CLEAR = "clear"  # clear the mapped real slot and forget the mapping
IGNORE = "ignore"  # a local credential sits there; leave it alone
NOOP = "noop"  # nothing to clear


def load_map(stored: Any) -> dict[int, int]:
    """Read the stored mapping (string keys) into int -> int."""
    result: dict[int, int] = {}
    if not isinstance(stored, Mapping):
        return result
    for virtual, real in stored.items():
        try:
            virtual_int = int(virtual)
            real_int = int(real)
        except (TypeError, ValueError):
            continue
        if virtual_int >= 0 and real_int >= 0:
            result[virtual_int] = real_int
    return result


def dump_map(mapping: Mapping[int, int]) -> dict[str, int]:
    """The stored shape: string keys, stable ints."""
    return {str(virtual): int(real) for virtual, real in sorted(mapping.items())}


def real_of(virtual: int, mapping: Mapping[int, int]) -> int | None:
    """The real slot a virtual slot maps to, or None when it passes through."""
    return mapping.get(virtual)


def virtual_of(real: int, mapping: Mapping[int, int]) -> int | None:
    """The virtual slot a real slot belongs to, or None when it is local."""
    for virtual, mapped in mapping.items():
        if mapped == real:
            return virtual
    return None


def _free_real(
    mapping: Mapping[int, int],
    local_pins: set[int],
    floor: int,
    capacity: int,
) -> int | None:
    """Lowest free real slot that no local credential and no mapping uses."""
    taken = set(local_pins) | set(mapping.values())
    for slot in range(floor, capacity):
        if slot not in taken:
            return slot
    return None


def resolve_write(
    virtual: int,
    *,
    mapping: Mapping[int, int],
    local_pins: set[int],
    floor: int,
    capacity: int,
) -> tuple[str, int | None, str | None]:
    """Where an app-driven PIN write should land.

    Returns (outcome, real slot, reason). The slot is set for PASS, REUSE and
    MOVE; the reason is set for BLOCKED.
    """
    if not isinstance(virtual, int) or isinstance(virtual, bool) or virtual < 0:
        return BLOCKED, None, "not a valid slot number"
    mapped = real_of(virtual, mapping)
    if mapped is not None:
        return REUSE, mapped, None
    if virtual < floor:
        return (
            BLOCKED,
            None,
            f"slot {virtual} is reserved for the master credential",
        )
    if virtual >= capacity:
        return BLOCKED, None, f"slot {virtual} is outside the lock's PIN capacity"
    taken = set(local_pins) | set(mapping.values())
    if virtual not in taken:
        return PASS, virtual, None
    relocated = _free_real(mapping, local_pins, floor, capacity)
    if relocated is None:
        return BLOCKED, None, "every free slot holds a local credential"
    return MOVE, relocated, None


def resolve_clear(
    virtual: int,
    *,
    mapping: Mapping[int, int],
    local_pins: set[int],
) -> tuple[str, int | None]:
    """What an app-driven clear should do.

    Returns (outcome, real slot). Only CLEAR carries a slot.
    """
    mapped = real_of(virtual, mapping)
    if mapped is not None:
        return CLEAR, mapped
    if virtual in local_pins:
        return IGNORE, None
    return NOOP, None
