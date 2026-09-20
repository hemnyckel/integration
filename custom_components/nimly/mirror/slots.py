"""The slot table: names and occupancy for the lock's PIN/RFID slots.

Stored in the mirror entry's options under "slots" - the same shape the former
onesti_lock integration used, so an existing table is imported once.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..const import DEFAULT_SLOT

_LOGGER = logging.getLogger(__name__)

SLOTS_OPTION = "slots"


class SlotTable:
    """Slot data for one lock: name, has_pin and has_rfid per slot."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._slots: dict[str, dict[str, Any]] = {}
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

    def get(self, slot: int) -> dict[str, Any]:
        return {**DEFAULT_SLOT, **self._slots.get(str(slot), {})}

    def name(self, slot: int, *, fallback: bool = True) -> str:
        name = str(self.get(slot).get("name") or "")
        if name or not fallback:
            return name
        return f"Slot {slot}"

    def occupied(self, slot: int) -> bool:
        data = self.get(slot)
        return bool(data.get("name") or data.get("has_pin") or data.get("has_rfid"))

    def set_name(self, slot: int, name: str) -> None:
        self._slots.setdefault(str(slot), {**DEFAULT_SLOT})["name"] = name
        self._save()

    def mark_pin(self, slot: int, present: bool) -> None:
        self._slots.setdefault(str(slot), {**DEFAULT_SLOT})["has_pin"] = present
        self._save()

    def mark_rfid(self, slot: int, present: bool) -> None:
        self._slots.setdefault(str(slot), {**DEFAULT_SLOT})["has_rfid"] = present
        self._save()

    def items(self) -> list[tuple[int, dict[str, Any]]]:
        return sorted(
            ((int(slot), dict(data)) for slot, data in self._slots.items()),
            key=lambda item: item[0],
        )

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {str(slot): dict(data) for slot, data in self.items()}

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
                if merged.get("name") or merged.get("has_pin") or merged.get("has_rfid"):
                    self._slots[str(slot)] = merged
                    imported += 1
            if imported:
                self._save()
            return imported
        return 0
