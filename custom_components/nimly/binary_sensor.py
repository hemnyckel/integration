"""Binary sensor platform for nimly — routes each config entry to its layer."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_TYPE, TYPE_BRIDGE, TYPE_MIRROR


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_type = entry.data.get(CONF_TYPE)
    if entry_type in (TYPE_MIRROR, TYPE_BRIDGE):
        from .mirror.binary_sensor import async_setup_entry as async_setup_mirror

        await async_setup_mirror(hass, entry, async_add_entities)
