"""Sensors for the mirror layer: last event, journal, slots, guests and facts."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import DOMAIN
from .coordinator import MirrorCoordinator
from .entity import MirrorEntity
from .facts import capability_summary
from .fingers import finger_state


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            MirrorSlots(coordinator),
            MirrorLastEvent(coordinator),
            MirrorLockFacts(coordinator),
            MirrorJournal(coordinator),
            MirrorGuests(coordinator),
        ]
    )
    _setup_slot_sensors(coordinator, async_add_entities)


def _setup_slot_sensors(
    coordinator: MirrorCoordinator, async_add_entities: AddEntitiesCallback
) -> None:
    """One sensor per known slot, added as slots appear and pruned when gone."""
    known: set[int] = set()

    def _sync_slots() -> None:
        active = {slot for slot, _data in coordinator.slots.items()}
        registry = er.async_get(coordinator.hass)
        prefix = f"{coordinator.entry.entry_id}_slot_"
        for entry in er.async_entries_for_config_entry(
            registry, coordinator.entry.entry_id
        ):
            unique_id = entry.unique_id or ""
            if not unique_id.startswith(prefix):
                continue
            try:
                slot = int(unique_id[len(prefix) :])
            except ValueError:
                continue
            if slot not in active:
                registry.async_remove(entry.entity_id)
                known.discard(slot)

        new = [
            SlotSensor(coordinator, slot)
            for slot, _data in coordinator.slots.items()
            if slot not in known
        ]
        if new:
            known.update(entity.slot for entity in new)
            async_add_entities(new)

    coordinator.slots.add_listener(_sync_slots)
    _sync_slots()


class MirrorSlots(MirrorEntity, SensorEntity):
    """All known slots at a glance: names and credential types."""

    # The collection is the household's "Nycklar" (Keys); a slot is still a
    # slot and the entity id stays *_slots.
    _attr_name = "Nycklar"
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_slots"

    @property
    def native_value(self) -> str:
        occupied = sum(
            1
            for slot, _data in self.coordinator.slots.items()
            if self.coordinator.slots.occupied(slot)
        )
        # The household's language, not "0 occupied".
        return f"{occupied} upptagen" if occupied == 1 else f"{occupied} upptagna"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rows = []
        for slot, data in self.coordinator.slots.items():
            rows.append(
                {
                    "slot": slot,
                    "name": str(data.get("name") or ""),
                    "has_pin": bool(data.get("has_pin")),
                    "has_fingerprint": bool(data.get("has_fingerprint")),
                    "has_rfid": bool(data.get("has_rfid")),
                    "finger_used": bool(data.get("finger_used")),
                    "finger_state": finger_state(data),
                    "fingers": [dict(item) for item in (data.get("fingers") or [])],
                    "credentials": self.coordinator.slots.credentials(slot),
                }
            )
        return {"slots": rows, **self.door_identity()}


class SlotSensor(MirrorEntity, SensorEntity):
    """One lock slot: its name and which credential types it holds."""

    _attr_icon = "mdi:account-key"

    def __init__(self, coordinator: MirrorCoordinator, slot: int) -> None:
        super().__init__(coordinator)
        self.slot = slot
        self._attr_name = f"Slot {slot}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_slot_{slot}"

    @property
    def native_value(self) -> str:
        data = self.coordinator.slots.get(self.slot)
        name = str(data.get("name") or "")
        if name:
            return name
        return "Upptagen" if self.coordinator.slots.occupied(self.slot) else "Ledig"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.slots.get(self.slot)
        return {
            "slot": self.slot,
            "has_pin": bool(data.get("has_pin")),
            "has_fingerprint": bool(data.get("has_fingerprint")),
            "has_rfid": bool(data.get("has_rfid")),
            "finger_used": bool(data.get("finger_used")),
            "finger_state": finger_state(data),
            "fingers": [dict(item) for item in (data.get("fingers") or [])],
            "credentials": self.coordinator.slots.credentials(self.slot),
        }


class MirrorLastEvent(MirrorEntity, SensorEntity):
    """The most recent event on the lock (action, source, slot)."""

    _attr_name = "Last event"
    _attr_icon = "mdi:lock-clock"

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_event"

    @property
    def native_value(self) -> str | None:
        event = self.coordinator.last_event
        return event.get("action") if event else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self.coordinator.last_event or {})


class MirrorLockFacts(MirrorEntity, SensorEntity):
    """What the lock itself reports: capabilities and settings, over Zigbee."""
    # Household word; the entity id stays *_lock_facts.
    _attr_name = "Låsdata"
    _attr_icon = "mdi:information-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_lock_facts"

    @property
    def native_value(self) -> str | None:
        return capability_summary(self.coordinator.lock_facts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        facts = self.coordinator.lock_facts
        if not facts:
            return self.door_identity()
        auto = facts.get("auto_relock_time")
        return {
            "total_users": facts.get("num_of_total_users_supported"),
            "pin_users": facts.get("num_of_pin_users_supported"),
            "rfid_users": facts.get("num_of_rfid_users_supported"),
            "auto_lock": None if auto is None else bool(auto),
            "sound_volume": facts.get("sound_volume"),
            "door_state": facts.get("door_state"),
            "updated": self.coordinator.lock_facts_at,
            **self.door_identity(),
        }


class MirrorJournal(MirrorEntity, SensorEntity):
    """The lock's timeline: who opened it, when and how."""

    _attr_name = "Journal"
    _attr_icon = "mdi:book-open-variant"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_journal"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.journal:
            return None
        entry = self.coordinator.journal[-1]
        who = entry.get("name") or entry.get("detail")
        if not who and entry.get("slot") is not None:
            who = f"slot {entry['slot']}"
        if not who:
            who = entry.get("source") or ""
        action = str(entry.get("action") or "")
        return " · ".join(part for part in (action, str(who)) if part)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self.coordinator.journal_summary()
        return {
            "count_total": summary.get("total"),
            "count_last_24h": summary.get("last_24h"),
            "entries": list(self.coordinator.journal[-10:]),
        }


class MirrorGuests(MirrorEntity, SensorEntity):
    """The people and their codes: simple windows and recurring schedules."""

    # User-facing name only; the entity id stays *_guests (renaming it would
    # break the dashboard and the app). "Personer" is the app's word.
    _attr_name = "Personer"
    _attr_icon = "mdi:account-multiple-check"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_guests"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.guests)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rows = self.coordinator.guest_rows()
        order = sorted(rows, key=lambda key: int(key) if key.isdigit() else 0)
        return {
            **self.door_identity(),
            "guests": [rows[key] for key in order],
        }
