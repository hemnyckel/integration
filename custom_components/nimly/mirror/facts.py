"""Pure helpers for the mirror's local lock facts.

No Home Assistant imports: the unit tests load this module by path.
"""

from __future__ import annotations

import re
from typing import Any

# A slot table imported from the onesti_lock integration carries generic names
# like "App slot 10"; they hold no person and must not stop the naming flow from
# asking again.
_PLACEHOLDER_SLOT_NAME = re.compile(r"app slot\s*\d+", re.IGNORECASE)


def placeholder_slot_name(name: Any) -> bool:
    """True when a slot name is empty or the import's generic placeholder."""
    text = str(name or "").strip()
    return not text or bool(_PLACEHOLDER_SLOT_NAME.fullmatch(text))


def compute_settings_drift(
    facts: dict[str, Any],
    app_auto_lock: bool | None,
    app_volume: int | None,
) -> dict[str, dict[str, Any]]:
    """Settings where the lock's own value differs from the app's record."""
    drift: dict[str, dict[str, Any]] = {}
    auto = facts.get("auto_relock_time")
    if (
        auto is not None
        and app_auto_lock is not None
        and bool(auto) != bool(app_auto_lock)
    ):
        drift["auto_lock"] = {"lock": bool(auto), "app": bool(app_auto_lock)}
    volume = facts.get("sound_volume")
    if volume is not None and app_volume is not None and int(volume) != int(app_volume):
        drift["sound_volume"] = {"lock": int(volume), "app": int(app_volume)}
    return drift


def capability_summary(facts: dict[str, Any]) -> str | None:
    """A compact human summary of the lock's user-slot capabilities."""
    pin = facts.get("num_of_pin_users_supported")
    rfid = facts.get("num_of_rfid_users_supported")
    total = facts.get("num_of_total_users_supported")
    if pin is None and rfid is None and total is None:
        return None
    parts: list[str] = []
    if pin is not None:
        parts.append(f"{pin} PIN")
    if rfid is not None:
        parts.append(f"{rfid} RFID")
    if total is not None:
        parts.append(f"{total} total")
    return " · ".join(parts)


VENDOR_VOLUMES: dict[int, str] = {0: "silent", 1: "low", 2: "high"}


def vendor_volume(level: int) -> str | None:
    """The vendor cloud's name for a lock volume level (0-2)."""
    try:
        return VENDOR_VOLUMES.get(int(level))
    except (TypeError, ValueError):
        return None


def suggest_user_name(
    wanted_types: set[str],
    used_names: set[str],
    users: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> str | None:
    """A cloud user name for a slot, when exactly one user fits.

    The cloud maps users to credential types but never exposes slot numbers
    (the gateway translates internally), so the match is deliberately
    conservative: only a single user holding one of the slot's credential
    types, with a name no local slot uses yet, is suggested.
    """
    types_by_user: dict[str, set[str]] = {}
    for entry in entries:
        user_id = str(entry.get("userId") or "")
        kind = str(entry.get("type") or "")
        if user_id and kind:
            types_by_user.setdefault(user_id, set()).add(kind)
    candidates: list[str] = []
    for user in users:
        name = str(user.get("name") or user.get("firstName") or "")
        user_id = str(user.get("id") or "")
        if not name or name.lower() in used_names:
            continue
        if wanted_types & types_by_user.get(user_id, set()):
            candidates.append(name)
    if len(candidates) == 1:
        return candidates[0]
    return None
