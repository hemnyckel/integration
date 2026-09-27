"""The app side mirror lock."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
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
    async_add_entities([MirrorLock(coordinator)])


class MirrorLock(MirrorEntity, LockEntity):
    """The app side lock (the emulator) - controls both the app and the real lock."""

    _attr_name = None

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_app_lock"

    @property
    def is_locked(self) -> bool | None:
        return self.coordinator.app_locked

    @property
    def available(self) -> bool:
        return self.coordinator.bridge_online and super().available

    async def async_lock(self, **kwargs: Any) -> None:
        await self.coordinator.async_command_lock(True)

    async def async_unlock(self, **kwargs: Any) -> None:
        await self.coordinator.async_command_lock(False)
