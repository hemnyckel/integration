"""Bridge connectivity as a binary sensor."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
        async_add_entities([BridgeOnline(coordinator)])
        return
    async_add_entities([MirrorBridge(coordinator)])


class MirrorBridge(MirrorEntity, BinarySensorEntity):
    """The emulator/bridge responds (otherwise the app side is down)."""

    _attr_name = "Bridge online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_bridge_online"

    @property
    def is_on(self) -> bool:
        return self.coordinator.bridge_online


class BridgeOnline(BridgeEntity, BinarySensorEntity):
    """The bridge responds over MQTT."""

    _attr_name = "Bridge online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"bridge_{coordinator.address}_online"

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("online"))
