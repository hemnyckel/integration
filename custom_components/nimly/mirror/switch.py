"""Switches: master mirroring, one per channel ("sliders") and app auto-lock."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import CHANNEL_ICONS, CHANNEL_LABELS, CHANNELS, DOMAIN
from .coordinator import MirrorCoordinator
from .entity import MirrorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MirrorCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [MirrorMaster(coordinator)]
    entities += [MirrorChannel(coordinator, key) for key in CHANNELS]
    entities.append(MirrorAppAutoLock(coordinator))
    async_add_entities(entities)


class MirrorMaster(MirrorEntity, SwitchEntity):
    """The whole mirroring on/off."""

    _attr_name = "Mirroring"
    _attr_icon = "mdi:swap-horizontal-bold"

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_master"

    @property
    def is_on(self) -> bool:
        return self.coordinator.master_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_master(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_master(False)


class MirrorChannel(MirrorEntity, SwitchEntity):
    """One channel - the user on/off slider for a mirrored feature."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MirrorCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = CHANNEL_LABELS.get(key, key)
        if icon := CHANNEL_ICONS.get(key):
            self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.entry.entry_id}_ch_{key}"

    @property
    def is_on(self) -> bool:
        return self.coordinator.channel(self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_channel(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_channel(self._key, False)


class MirrorAppAutoLock(MirrorEntity, SwitchEntity):
    """Auto-lock on the app side (sets the real lock when the channel is on)."""

    _attr_name = "App auto-lock"
    _attr_icon = "mdi:timer-lock-outline"

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_app_autolock"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.app_autolock

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_autolock(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_autolock(False)
