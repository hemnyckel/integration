"""The slot table: names, occupancy and credentials for the lock's slots.

Stored in the mirror entry's options under "slots" - the same shape the former
onesti_lock integration used, so an existing table is imported once.
"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable

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


def _record(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """A slot record with its own copy of the fingerprint labels.

    ``DEFAULT_SLOT`` carries an empty list; a list shared between slots would
    let one slot's labels leak into every other, so each record gets a fresh
    list and each label a fresh dict.
    """
    merged = {**DEFAULT_SLOT, **(data or {})}
    merged["fingers"] = [
        dict(item) for item in (merged.get("fingers") or []) if isinstance(item, dict)
    ]
    return merged


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
            str(slot): _record(data) for slot, data in stored.items()
        }

    def _save(self) -> None:
        # The inner dicts are copied because async_update_entry only writes to
        # .storage when the new options compare unequal to entry.options.
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                SLOTS_OPTION: {slot: _record(data) for slot, data in self._slots.items()},
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
        return _record(self._slots.get(str(slot)))

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
        self._slots.setdefault(str(slot), _record())["name"] = name
        self._save()
        self._notify()

    def mark_pin(self, slot: int, present: bool) -> None:
        self._slots.setdefault(str(slot), _record())["has_pin"] = present
        self._save()
        self._notify()

    def mark_rfid(self, slot: int, present: bool) -> None:
        self._slots.setdefault(str(slot), _record())["has_rfid"] = present
        self._save()
        self._notify()

    def mark_fingerprint(self, slot: int, present: bool) -> None:
        self._slots.setdefault(str(slot), _record())["has_fingerprint"] = present
        self._save()
        self._notify()

    def mark_credential(self, slot: int, kind: str) -> bool:
        """Learn a credential type from a usage event. True when it was new."""
        key = _CREDENTIAL_KEYS.get(kind)
        if key is None:
            return False
        data = self._slots.setdefault(str(slot), _record())
        if kind == "fingerprint":
            # A used finger is the only proof that its template exists.
            data["finger_used"] = True
        if data.get(key):
            return False
        data[key] = True
        self._save()
        self._notify()
        return True

    def finger_confirmed(self, slot: int) -> bool:
        """True only when a finger in this slot has actually opened the door."""
        return bool(self.get(slot).get("finger_used"))

    def finger_labels(self, slot: int) -> list[str]:
        """The labels recorded in a slot, in enrolment order."""
        return [
            str(item.get("label"))
            for item in (self.get(slot).get("fingers") or [])
            if item.get("label")
        ]

    def add_finger(self, slot: int, label: str, enrolled: str) -> None:
        """Record a claimed finger for a slot.

        An enrolment is a claim, never a proof: the lock reports nothing while
        it runs, so only a later real use (``mark_credential``) confirms it.
        """
        data = self._slots.setdefault(str(slot), _record())
        data.setdefault("fingers", []).append(
            {"label": str(label), "enrolled": str(enrolled)}
        )
        data["has_fingerprint"] = True
        self._save()
        self._notify()

    def clear_fingers(self, slot: int) -> None:
        """Forget a slot's fingerprint claim after the template was cleared."""
        key = str(slot)
        data = self._slots.get(key)
        if data is None:
            return
        data["fingers"] = []
        data["has_fingerprint"] = False
        data["finger_used"] = False
        # A slot that holds nothing else (no name, PIN or tag) leaves the table:
        # an empty record would only surface as a vacant slot everywhere.
        if (
            not data.get("name")
            and not data.get("has_pin")
            and not data.get("has_rfid")
        ):
            self._slots.pop(key, None)
        self._save()
        self._notify()

    def relabel_finger(
        self, slot: int, label: str, previous: str | None = None
    ) -> bool:
        """Rename a recorded finger; writes nothing to the lock."""
        data = self._slots.get(str(slot))
        if data is None:
            return False
        for item in data.get("fingers") or []:
            if previous is None or item.get("label") == previous:
                item["label"] = str(label)
                self._save()
                self._notify()
                return True
        return False

    def clear(self, slot: int) -> None:
        """Forget everything local about a slot (after its credential is cleared)."""
        if self._slots.pop(str(slot), None) is not None:
            self._save()
            self._notify()

    def correct_fingerprints(self) -> int:
        """Drop fingerprint hints that no usage ever confirmed *and* no label.

        A labelled enrolment is a deliberate claim and survives a restart; only
        a bare ``has_fingerprint`` mark from before labels existed is dropped
        when no use ever confirmed it. Returns slots corrected.
        """
        corrected = 0
        for data in self._slots.values():
            if (
                data.get("has_fingerprint")
                and not data.get("finger_used")
                and not data.get("fingers")
            ):
                data["has_fingerprint"] = False
                corrected += 1
        if corrected:
            self._save()
            self._notify()
        return corrected

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
                merged = _record(data)
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
