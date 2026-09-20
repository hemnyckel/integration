"""Services for the mirror layer."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_IEEE = "set_ieee"
SERVICE_OTA_INSTALL = "ota_install"
SERVICE_PROVISION = "provision_wifi"
SERVICE_SET_SLOT_NAME = "set_slot_name"

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

    async def _async_handle_provision(call: ServiceCall) -> None:
        from .improv_ble import async_provision

        await async_provision(
            hass,
            call.data["address"],
            call.data["ssid"],
            call.data.get("password") or "",
        )

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


async def async_unload_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, SERVICE_SET_IEEE)
    hass.services.async_remove(DOMAIN, SERVICE_OTA_INSTALL)
    hass.services.async_remove(DOMAIN, SERVICE_PROVISION)
    hass.services.async_remove(DOMAIN, SERVICE_SET_SLOT_NAME)
