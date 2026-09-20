"""nimly — the Nimly lock product as a Home Assistant integration.

Entry types: ``cloud`` (the vendor account), ``mirror`` (the emulator mirror) and
``bridge`` (the C3 bridge provisioning). The merge ports the two former integrations into
one package, layer by layer; ``cloud`` is ported first.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .cloud.services import async_setup_services
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_LOCATION_ID,
    CONF_REFRESH_TOKEN,
    CONF_TYPE,
    DOMAIN,
    TYPE_CLOUD,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS_CLOUD = ["sensor", "binary_sensor"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the services once, at integration setup."""
    await async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_type = entry.data.get(CONF_TYPE)
    if entry_type == TYPE_CLOUD:
        return await _async_setup_cloud(hass, entry)
    _LOGGER.error("nimly: entry type %s is not ported yet", entry_type)
    return False


async def _async_setup_cloud(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .cloud.api import NimlyCloudApi
    from .cloud.coordinator import NimlyCloudCoordinator

    def _save_tokens(access: str, refresh: str) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_ACCESS_TOKEN: access, CONF_REFRESH_TOKEN: refresh},
        )

    api = NimlyCloudApi(
        hass,
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        on_tokens=_save_tokens,
    )
    coordinator = NimlyCloudCoordinator(hass, entry, api, entry.data[CONF_LOCATION_ID])
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_CLOUD)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.data.get(CONF_TYPE) != TYPE_CLOUD:
        return False
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS_CLOUD)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply an options change (for example a new scan interval)."""
    await hass.config_entries.async_reload(entry.entry_id)
