"""The slot table: names, occupancy and credentials for the lock's slots.

Stored in the mirror entry's options under "slots" - the same shape the former
onesti_lock integration used, so an existing table is imported once.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..const import DEFAULT_SLOT

_LOGGER = logging.getLogger(__name__)

SLOTS_OPTION = "slots"

_CREDENTIAL_KEYS = {
    "pin": "has_pin",
    "fingerprint": "has_fingerprint",
    "rfid": "has_rfid",
}


class SlotTable:
    """Slot data for one lock: name and credential types per slot."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._slots: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._load()

    def _load(self) -> None:
        stored = self.entry.options.get(SLOTS_OPTION) or {}
        self._slots = {
            str(slot): {**DEFAULT_SLOT, **data} for slot, data in stored.items()
        }

    def _save(self) -> None:
        # The inner dicts are copied because async_update_entry only writes to
        # .storage when the new options compare unequal to entry.options.
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                SLOTS_OPTION: {slot: dict(data) for slot, data in self._slots.items()},
            },
        )

    # -- listeners ----------------------------------------------------------

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()

    # -- data access --------------------------------------------------------

    def get(self, slot: int) -> dict[str, Any]:
        return {**DEFAULT_SLOT, **self._slots.get(str(slot), {})}

    def name(self, slot: int, *, fallback: bool = True) -> str:
        name = str(self.get(slot).get("name") or "")
        if name or not fallback:
            return name
        return f"Slot {slot}"

    def credentials(self, slot: int) -> list[str]:
        data = self.get(slot)
        return [kind for kind, key in _CREDENTIAL_KEYS.items() if data.get(key)]

    def occupied(self, slot: int) -> bool:
        return bool(self.get(slot).get("name")) or bool(self.credentials(slot))

    def items(self) -> list[tuple[int, dict[str, Any]]]:
        return sorted(
            ((int(slot), dict(data)) for slot, data in self._slots.items()),
            key=lambda item: item[0],
        )

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {str(slot): dict(data) for slot, data in self.items()}

    # -- mutations ----------------------------------------------------------

    def set_name(self, slot: int, name: str) -> None:
        self._slots.setdefault(str(slot), {**DEFAULT_SLOT})["name"] = name
        self._save()
        self._notify()

    def mark_pin(self, slot: int, present: bool) -> None:
        self._slots.setdefault(str(slot), {**DEFAULT_SLOT})["has_pin"] = present
        self._save()
        self._notify()

    def mark_rfid(self, slot: int, present: bool) -> None:
        self._slots.setdefault(str(slot), {**DEFAULT_SLOT})["has_rfid"] = present
        self._save()
        self._notify()

    def mark_credential(self, slot: int, kind: str) -> bool:
        """Learn a credential type from a usage event. True when it was new."""
        key = _CREDENTIAL_KEYS.get(kind)
        if key is None:
            return False
        data = self._slots.setdefault(str(slot), {**DEFAULT_SLOT})
        if data.get(key):
            return False
        data[key] = True
        self._save()
        self._notify()
        return True

    def clear(self, slot: int) -> None:
        """Forget everything local about a slot (after its credential is cleared)."""
        if self._slots.pop(str(slot), None) is not None:
            self._save()
            self._notify()

    def import_from_onesti(self, hass: HomeAssistant, ieee: str) -> int:
        """Import slot names and occupancy from an onesti_lock entry, once.

        Only fills gaps: slots this table already knows are left alone. Returns
        the number of imported slots.
        """
        for entry in hass.config_entries.async_entries("onesti_lock"):
            if str(entry.data.get("ieee", "")).lower() != ieee.lower():
                continue
            stored = entry.options.get(SLOTS_OPTION) or {}
            imported = 0
            for slot, data in stored.items():
                if str(slot) in self._slots:
                    continue
                merged = {**DEFAULT_SLOT, **data}
                if any(
                    merged.get(key)
                    for key in ("name", "has_pin", "has_fingerprint", "has_rfid")
                ):
                    self._slots[str(slot)] = merged
                    imported += 1
            if imported:
                self._save()
                self._notify()
            return imported
        return 0
