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
            MirrorLastEvent(coordinator),
            MirrorLastPin(coordinator),
            MirrorBattery(coordinator),
            MirrorFirmware(coordinator),
            MirrorBridgeFirmware(coordinator),
            MirrorLastError(coordinator),
        ]
    )


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
