"""Sensor platform for hemnyckel — routes each config entry to its layer."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    from .mirror.sensor import async_setup_entry as async_setup_mirror

    await async_setup_mirror(hass, entry, async_add_entities)
