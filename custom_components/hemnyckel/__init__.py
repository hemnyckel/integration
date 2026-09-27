"""hemnyckel — Hemnyckel's Home Assistant integration for Nimly locks.

Entry types: ``mirror`` (the emulator mirror against the real lock) and ``bridge``
(the ESP32 bridge provisioning).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import (
    CHANNELS,
    CONF_CHANNELS,
    CONF_ENABLED,
    CONF_LOCK_ENTITY,
    CONF_PREFIX,
    CONF_TYPE,
    DEFAULT_CHANNELS,
    DEFAULT_PREFIX,
    DOMAIN,
    TYPE_BRIDGE,
    TYPE_MIRROR,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS_MIRROR = ["lock", "sensor", "binary_sensor", "switch", "number", "update"]
PLATFORMS_BRIDGE = ["binary_sensor", "sensor", "update"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_type = entry.data.get(CONF_TYPE)
    if entry_type == TYPE_MIRROR:
        return await _async_setup_mirror(hass, entry)
    if entry_type == TYPE_BRIDGE:
        return await _async_setup_bridge(hass, entry)
    _LOGGER.error("hemnyckel: unknown entry type %s", entry_type)
    return False

    from .mirror.coordinator import MirrorCoordinator
    from .mirror.services import async_setup_services as async_setup_mirror_services

    channels = dict(DEFAULT_CHANNELS)
    channels.update(entry.data.get(CONF_CHANNELS) or {})
    channels.update(entry.options.get(CONF_CHANNELS) or {})

    coordinator = MirrorCoordinator(
        hass,
        entry,
        lock_entity_id=entry.data[CONF_LOCK_ENTITY],
        prefix=entry.options.get(
            CONF_PREFIX, entry.data.get(CONF_PREFIX, DEFAULT_PREFIX)
        ),
        channels=channels,
    )
    coordinator.master_enabled = bool(entry.options.get(CONF_ENABLED, True))
    await coordinator.async_setup()
    # Start the coordinator's update loop: without it the periodic sync of
    # volume/auto-lock/battery and the OTA manifest never runs.
    await coordinator.async_config_entry_first_refresh()

    await async_setup_mirror_services(hass)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_MIRROR)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_setup_bridge(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .mirror.bridge import BridgeCoordinator

    bridge = BridgeCoordinator(hass, entry)
    await bridge.async_setup()
    await bridge.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = bridge
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_BRIDGE)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_type = entry.data.get(CONF_TYPE)
    if entry_type == TYPE_MIRROR:
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

    if entry_type == TYPE_BRIDGE:
        unload_ok = await hass.config_entries.async_unload_platforms(
            entry, PLATFORMS_BRIDGE
        )
        if unload_ok:
            bridge = hass.data[DOMAIN].pop(entry.entry_id)
            await bridge.async_shutdown()
        return unload_ok

    return False


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow removing devices that are not this integration's own.

    Only a device this integration does not manage (for example one left
    behind by another integration) can be deleted this way; our own devices
    stay protected, since they would only be recreated.
    """
    return DOMAIN not in {domain for domain, _value in device_entry.identifiers}


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The mirror channels changed in options -> reload the entry.

    The channel switches update the coordinator first and then write options; when
    nothing actually differs (only a runtime mirror) we do not reload.
    """
    coordinator: Any = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None and _runtime_matches(coordinator, entry):
        return
    await hass.config_entries.async_reload(entry.entry_id)


def _runtime_matches(coordinator: Any, entry: ConfigEntry) -> bool:
    if bool(entry.options.get(CONF_ENABLED, True)) != coordinator.master_enabled:
        return False
    channels = entry.options.get(CONF_CHANNELS) or {}
    return all(
        bool(channels.get(key, DEFAULT_CHANNELS.get(key, False)))
        == coordinator.channel(key)
        for key in CHANNELS
    )
