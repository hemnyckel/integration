"""Pure helpers for the mirror's local lock facts.

No Home Assistant imports: the unit tests load this module by path.
"""

from __future__ import annotations

from typing import Any


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
