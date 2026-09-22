"""Pure planning helpers for the cloud sync — no Home Assistant.

The sync decides what the vendor cloud is missing and, when it is uncertain,
proposes instead of writing (see docs/cloud-sync.md). These functions are the
parts of that judgement that carry no Home Assistant dependency, so the unit
tests can load them by path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# The cloud's guest validity is a coarse window; the real schedule lives in HA.
VALID_YEARS = 100


def validity(now: datetime | None = None) -> tuple[str, str]:
    """A from/to pair the cloud accepts: now, and the same date 100 years on."""
    start = now or datetime.now(timezone.utc)
    try:
        end = start.replace(year=start.year + VALID_YEARS)
    except ValueError:  # 29 February in a non-leap target year
        end = start.replace(year=start.year + VALID_YEARS, day=28)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def guest_row(guest: dict[str, Any]) -> dict[str, Any]:
    """One guest as the sensor and the service both present it.

    A single shape for consumers (the plan card included): the vendor's
    camelCase stays at the API edge.
    """
    return {
        "id": guest.get("id"),
        "name": guest.get("name"),
        "email": guest.get("email"),
        "valid_from": guest.get("validFrom"),
        "valid_to": guest.get("validTo"),
        "has_access": bool(guest.get("hasDoorlockAccess")),
        "has_pin": bool(guest.get("hasDoorlockPin")),
        "has_tag": bool(guest.get("hasDoorlockTag")),
        "has_fingerprint": bool(guest.get("hasDoorlockFingerprint")),
        "status": guest.get("updateStatus"),
    }


def same_named(name: str, guests: list[dict[str, Any]]) -> bool:
    """True when any cloud guest carries this name (used to flag conflicts)."""
    wanted = str(name or "").strip().casefold()
    if not wanted:
        return False
    return any(
        str(guest.get("name") or "").strip().casefold() == wanted for guest in guests
    )


def same_named_all(name: str, guests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every cloud guest with this name, in the order the account lists them."""
    wanted = str(name or "").strip().casefold()
    if not wanted:
        return []
    return [
        guest
        for guest in guests
        if str(guest.get("name") or "").strip().casefold() == wanted
    ]


def choose_identity(
    name: str,
    guests: list[dict[str, Any]],
    *,
    on_this_lock: set[str],
    linked: set[str],
) -> tuple[str, str]:
    """Which cloud identity a local guest should use, across every lock.

    One person, one identity, however many locks: a name that already has an
    access elsewhere is adopted here too — the access is added, never a second
    identity. A same-named identity holding an access on *this* lock that the
    catalog does not record as ours is a conflict for a human, because adopting
    it could hand the code to the wrong person.

    Returns ``(action, user_id)`` with action one of ``create``, ``adopt`` or
    ``conflict``.
    """
    candidates = same_named_all(name, guests)
    if not candidates:
        return "create", ""
    ours = [str(guest.get("id")) for guest in candidates if str(guest.get("id")) in linked]
    if len(ours) == 1:
        return "adopt", ours[0]
    if len(candidates) > 1:
        return "conflict", ""
    candidate = str(candidates[0].get("id") or "")
    if candidate in on_this_lock:
        return "conflict", ""
    return "adopt", candidate
