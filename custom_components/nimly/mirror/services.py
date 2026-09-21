"""Services for the mirror layer."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_IEEE = "set_ieee"
SERVICE_OTA_INSTALL = "ota_install"
SERVICE_PROVISION = "provision_wifi"
SERVICE_SET_SLOT_NAME = "set_slot_name"
SERVICE_SET_PIN = "set_pin"
SERVICE_CLEAR_SLOT = "clear_slot"
SERVICE_READ_LOCK_ATTRIBUTES = "read_lock_attributes"
SERVICE_SET_AUTO_LOCK = "set_auto_lock"
SERVICE_SET_SOUND_VOLUME = "set_sound_volume"

PROVISION_SCHEMA = vol.Schema(
    {
        vol.Required("address"): cv.string,
        vol.Required("ssid"): cv.string,
        vol.Optional("password", default=""): cv.string,
    }
)

SET_IEEE_SCHEMA = vol.Schema(
    {
        vol.Optional("ieee"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)

OTA_INSTALL_SCHEMA = vol.Schema(
    {
        vol.Required("url"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)

SET_SLOT_NAME_SCHEMA = vol.Schema(
    {
        vol.Required("slot"): vol.Coerce(int),
        vol.Required("name"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)

SET_PIN_SCHEMA = vol.Schema(
    {
        vol.Required("slot"): vol.Coerce(int),
        vol.Required("code"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)

CLEAR_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required("slot"): vol.Coerce(int),
        vol.Optional("entry_id"): cv.string,
    }
)

READ_LOCK_ATTRIBUTES_SCHEMA = vol.Schema(
    {
        vol.Optional("attributes"): [vol.Any(cv.string, vol.Coerce(int))],
        vol.Optional("entry_id"): cv.string,
    }
)

SET_AUTO_LOCK_SCHEMA = vol.Schema(
    {
        vol.Required("enabled"): cv.boolean,
        vol.Optional("entry_id"): cv.string,
    }
)

SET_SOUND_VOLUME_SCHEMA = vol.Schema(
    {
        vol.Required("level"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
        vol.Optional("entry_id"): cv.string,
    }
)


def _coordinators(hass: HomeAssistant, entry_id: str | None, capability: str):
    """The entries that can serve a call, filtered by entry and by capability.

    The data store also holds cloud coordinators, so a capability check keeps a
    service from being handed the wrong layer's object.
    """
    return [
        coord
        for key, coord in hass.data.get(DOMAIN, {}).items()
        if (entry_id is None or key == entry_id) and hasattr(coord, capability)
    ]


async def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_IEEE):
        return

    async def _async_handle_set_ieee(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        ieee = call.data.get("ieee")
        coordinators = _coordinators(hass, entry_id, "async_provision_ieee")
        if not coordinators:
            _LOGGER.error("set_ieee: no matching mirror (%s)", entry_id)
            return
        for coord in coordinators:
            await coord.async_provision_ieee(ieee)

    async def _async_handle_ota_install(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        url = call.data["url"]
        coordinators = _coordinators(hass, entry_id, "async_ota")
        if not coordinators:
            _LOGGER.error("ota_install: no matching mirror (%s)", entry_id)
            return
        for coord in coordinators:
            await coord.async_ota(url)

    async def _async_handle_set_slot_name(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        slot = int(call.data["slot"])
        name = str(call.data["name"])
        coordinators = _coordinators(hass, entry_id, "async_set_slot_name")
        if not coordinators:
            _LOGGER.error("set_slot_name: no matching mirror (%s)", entry_id)
            return
        for coord in coordinators:
            await coord.async_set_slot_name(slot, name)

    async def _async_handle_set_pin(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        slot = int(call.data["slot"])
        code = str(call.data["code"])
        name = call.data.get("name")
        coordinators = _coordinators(hass, entry_id, "async_set_slot_pin")
        if not coordinators:
            _LOGGER.error("set_pin: no matching mirror (%s)", entry_id)
            return
        for coord in coordinators:
            await coord.async_set_slot_pin(slot, code)
            if name:
                await coord.async_set_slot_name(slot, str(name))

    async def _async_handle_clear_slot(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        slot = int(call.data["slot"])
        coordinators = _coordinators(hass, entry_id, "async_clear_slot")
        if not coordinators:
            _LOGGER.error("clear_slot: no matching mirror (%s)", entry_id)
            return
        for coord in coordinators:
            await coord.async_clear_slot(slot)

    async def _async_handle_provision(call: ServiceCall) -> None:
        from .improv_ble import async_provision

        await async_provision(
            hass,
            call.data["address"],
            call.data["ssid"],
            call.data.get("password") or "",
        )

    async def _async_handle_read_lock_attributes(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        attributes = call.data.get("attributes")
        coordinators = _coordinators(hass, entry_id, "async_read_lock_attributes")
        if not coordinators:
            return {"error": "no matching mirror"}
        results: dict[str, Any] = {}
        for coord in coordinators:
            results[coord.entry.entry_id] = await coord.async_read_lock_attributes(
                attributes
            )
        return results

    async def _async_handle_set_auto_lock(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        enabled = bool(call.data["enabled"])
        coordinators = _coordinators(hass, entry_id, "async_set_lock_autolock")
        if not coordinators:
            return {"error": "no matching mirror"}
        results: dict[str, Any] = {}
        for coord in coordinators:
            results[coord.entry.entry_id] = await coord.async_set_lock_autolock(enabled)
        return results

    async def _async_handle_set_sound_volume(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        level = int(call.data["level"])
        coordinators = _coordinators(hass, entry_id, "async_set_lock_volume")
        if not coordinators:
            return {"error": "no matching mirror"}
        results: dict[str, Any] = {}
        for coord in coordinators:
            results[coord.entry.entry_id] = await coord.async_set_lock_volume(level)
        return results

    hass.services.async_register(
        DOMAIN, SERVICE_SET_IEEE, _async_handle_set_ieee, schema=SET_IEEE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_OTA_INSTALL, _async_handle_ota_install, schema=OTA_INSTALL_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PROVISION, _async_handle_provision, schema=PROVISION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SLOT_NAME,
        _async_handle_set_slot_name,
        schema=SET_SLOT_NAME_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PIN, _async_handle_set_pin, schema=SET_PIN_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_SLOT, _async_handle_clear_slot, schema=CLEAR_SLOT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_LOCK_ATTRIBUTES,
        _async_handle_read_lock_attributes,
        schema=READ_LOCK_ATTRIBUTES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_AUTO_LOCK,
        _async_handle_set_auto_lock,
        schema=SET_AUTO_LOCK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SOUND_VOLUME,
        _async_handle_set_sound_volume,
        schema=SET_SOUND_VOLUME_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, SERVICE_SET_IEEE)
    hass.services.async_remove(DOMAIN, SERVICE_OTA_INSTALL)
    hass.services.async_remove(DOMAIN, SERVICE_PROVISION)
    hass.services.async_remove(DOMAIN, SERVICE_SET_SLOT_NAME)
    hass.services.async_remove(DOMAIN, SERVICE_SET_PIN)
    hass.services.async_remove(DOMAIN, SERVICE_CLEAR_SLOT)
    hass.services.async_remove(DOMAIN, SERVICE_READ_LOCK_ATTRIBUTES)
    hass.services.async_remove(DOMAIN, SERVICE_SET_AUTO_LOCK)
    hass.services.async_remove(DOMAIN, SERVICE_SET_SOUND_VOLUME)
