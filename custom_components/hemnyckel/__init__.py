"""hemnyckel — a local-only Home Assistant integration for Nimly locks.

The lock's module lives on ZHA; this integration adds the slot table, the
journal, guest codes and the local services on top of it. The vendor app side
(the bridge and the emulator) has been removed, so nothing here depends on
MQTT, a cloud service or extra hardware.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import CONF_LOCK_ENTITY, CONF_TYPE, DOMAIN, TYPE_MIRROR

_LOGGER = logging.getLogger(__name__)

PLATFORMS_MIRROR = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_type = entry.data.get(CONF_TYPE)
    if entry_type != TYPE_MIRROR:
        _LOGGER.error("hemnyckel: unknown entry type %s", entry_type)
        return False

    from .mirror.coordinator import MirrorCoordinator
    from .mirror.services import async_setup_services as async_setup_mirror_services

    coordinator = MirrorCoordinator(
        hass,
        entry,
        lock_entity_id=entry.data[CONF_LOCK_ENTITY],
    )
    await coordinator.async_setup()
    # Start the coordinator's update loop so its data is primed.
    await coordinator.async_config_entry_first_refresh()

    await async_setup_mirror_services(hass)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_MIRROR)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_type = entry.data.get(CONF_TYPE)
    if entry_type != TYPE_MIRROR:
        return False

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS_MIRROR
    )
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

        from .mirror.coordinator import MirrorCoordinator

        if not any(
            isinstance(item, MirrorCoordinator)
            for item in hass.data[DOMAIN].values()
        ):
            from .mirror.services import async_unload_services

            await async_unload_services(hass)
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow removing devices that are not this integration's own.

    Only a device this integration does not manage (for example one left
    behind by another integration) can be deleted this way; our own devices
    stay protected, since they would only be recreated.
    """
    return DOMAIN not in {domain for domain, _value in device_entry.identifiers}
