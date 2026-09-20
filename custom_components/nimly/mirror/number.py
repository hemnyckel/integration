"""The app side sound volume as a number."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import DOMAIN
from .coordinator import MirrorCoordinator
from .entity import MirrorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MirrorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MirrorAppVolume(coordinator)])


class MirrorAppVolume(MirrorEntity, NumberEntity):
    """The app side sound volume (0-2). Sets the real lock when the channel is on."""

    _attr_name = "App volume"
    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = 0
    _attr_native_max_value = 2
    _attr_native_step = 1
    _attr_mode = "slider"

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_app_volume"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.app_volume

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_volume(int(value))
