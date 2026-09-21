"""Sensors for the mirror layer: last event, battery and diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import CONF_TYPE, DOMAIN, TYPE_BRIDGE
from .bridge import BridgeEntity
from .coordinator import MirrorCoordinator
from .entity import MirrorEntity
from .facts import capability_summary


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_TYPE) == TYPE_BRIDGE:
        async_add_entities([BridgeFirmware(coordinator)])
        return
    async_add_entities(
        [
            MirrorSlots(coordinator),
            MirrorLastEvent(coordinator),
            MirrorLastPin(coordinator),
            MirrorBattery(coordinator),
            MirrorFirmware(coordinator),
            MirrorBridgeFirmware(coordinator),
            MirrorLastError(coordinator),
            MirrorLockFacts(coordinator),
        ]
    )
    _setup_slot_sensors(coordinator, async_add_entities)


def _setup_slot_sensors(
    coordinator: MirrorCoordinator, async_add_entities: AddEntitiesCallback
) -> None:
    """One sensor per known slot, added as slots appear."""
    known: set[int] = set()

    def _add_new_slots() -> None:
        new = [
            SlotSensor(coordinator, slot)
            for slot, _data in coordinator.slots.items()
            if slot not in known
        ]
        if new:
            known.update(entity.slot for entity in new)
            async_add_entities(new)

    coordinator.slots.add_listener(_add_new_slots)
    _add_new_slots()


class MirrorSlots(MirrorEntity, SensorEntity):
    """All known slots at a glance: names and credential types."""

    _attr_name = "Slots"
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
        return f"{occupied} occupied"

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
                    "credentials": self.coordinator.slots.credentials(slot),
                }
            )
        return {"slots": rows}


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
        return "Occupied" if self.coordinator.slots.occupied(self.slot) else "Vacant"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.slots.get(self.slot)
        return {
            "slot": self.slot,
            "has_pin": bool(data.get("has_pin")),
            "has_fingerprint": bool(data.get("has_fingerprint")),
            "has_rfid": bool(data.get("has_rfid")),
            "credentials": self.coordinator.slots.credentials(self.slot),
        }


class MirrorLastEvent(MirrorEntity, SensorEntity):
    """The most recent event on the app side (action, source, slot)."""

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


class MirrorBattery(MirrorEntity, SensorEntity):
    """The app side battery level (mirrored from the real lock)."""

    _attr_name = "Battery (app)"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_battery"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.app_battery


class MirrorFirmware(MirrorEntity, SensorEntity):
    """The emulator's firmware version, when it reports one."""

    _attr_name = "Emulator firmware"
    _attr_icon = "mdi:chip"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_firmware"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.firmware


class MirrorBridgeFirmware(MirrorEntity, SensorEntity):
    """The bridge (Wi-Fi/MQTT) firmware version, from the retained nimly/info."""

    _attr_name = "Bridge firmware"
    _attr_icon = "mdi:chip"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_bridge_firmware"

    @property
    def native_value(self) -> str | None:
        info = self.coordinator.bridge_info
        value = info.get("fw")
        return value if isinstance(value, str) else None


class MirrorLastError(MirrorEntity, SensorEntity):
    """The most recent mirroring error (for troubleshooting)."""

    _attr_name = "Last error"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_error"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.last_error


class BridgeFirmware(BridgeEntity, SensorEntity):
    """The bridge firmware version (from nimly/info)."""

    _attr_name = "Bridge firmware"
    _attr_icon = "mdi:chip"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"bridge_{coordinator.address}_firmware"

    @property
    def native_value(self) -> str | None:
        value = ((self.coordinator.data or {}).get("info") or {}).get("fw")
        return value if isinstance(value, str) else None


class MirrorLastPin(MirrorEntity, SensorEntity):
    """Last PIN event mirrored from the app side (slot + result)."""

    _attr_name = "Last PIN event"
    _attr_icon = "mdi:dialpad"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_pin"

    @property
    def native_value(self) -> str | None:
        pin = self.coordinator.last_pin
        return pin.get("event") if pin else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self.coordinator.last_pin or {})


class MirrorLockFacts(MirrorEntity, SensorEntity):
    """What the lock itself reports: capabilities and settings, over Zigbee."""

    _attr_name = "Lock facts"
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
            return {}
        auto = facts.get("auto_relock_time")
        return {
            "total_users": facts.get("num_of_total_users_supported"),
            "pin_users": facts.get("num_of_pin_users_supported"),
            "rfid_users": facts.get("num_of_rfid_users_supported"),
            "auto_lock": None if auto is None else bool(auto),
            "sound_volume": facts.get("sound_volume"),
            "door_state": facts.get("door_state"),
            "app_auto_lock": self.coordinator.app_autolock,
            "app_volume": self.coordinator.app_volume,
            "settings_drift": dict(self.coordinator.settings_drift),
            "updated": self.coordinator.lock_facts_at,
        }
