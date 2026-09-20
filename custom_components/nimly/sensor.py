"""Sensor platform for nimly — routes each config entry to its layer."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_TYPE, TYPE_CLOUD


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if entry.data.get(CONF_TYPE) == TYPE_CLOUD:
        from .cloud.sensor import async_setup_entry as async_setup_cloud

        await async_setup_cloud(hass, entry, async_add_entities)
