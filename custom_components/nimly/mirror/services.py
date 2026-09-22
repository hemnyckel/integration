"""Services for the mirror layer."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_IEEE = "set_ieee"
SERVICE_LINK_CREDENTIAL = "link_credential"
SERVICE_OTA_INSTALL = "ota_install"
SERVICE_PROVISION = "provision_wifi"
SERVICE_SET_SLOT_NAME = "set_slot_name"
SERVICE_SET_PIN = "set_pin"
SERVICE_CLEAR_SLOT = "clear_slot"
SERVICE_READ_LOCK_ATTRIBUTES = "read_lock_attributes"
SERVICE_SET_AUTO_LOCK = "set_auto_lock"
SERVICE_SET_SOUND_VOLUME = "set_sound_volume"
SERVICE_FETCH_JOURNAL = "fetch_journal"
SERVICE_CREATE_GUEST = "create_guest_code"
SERVICE_CREATE_RECURRING_GUEST = "create_recurring_guest"
SERVICE_UPDATE_GUEST = "update_guest"
SERVICE_REVOKE_GUEST = "revoke_guest_code"
SERVICE_LIST_GUESTS = "list_guests"
SERVICE_RESET_APP = "reset_app_registration"

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

LINK_CREDENTIAL_SCHEMA = vol.Schema(
    {
        vol.Required("slot"): vol.Coerce(int),
        vol.Required("access_type"): vol.In(["pin", "tag", "finger"]),
        vol.Required("user_id"): cv.string,
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

FETCH_JOURNAL_SCHEMA = vol.Schema(
    {
        vol.Optional("limit", default=50): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=500)
        ),
        vol.Optional("person"): cv.string,
        vol.Optional("slot"): vol.Coerce(int),
        vol.Optional("action"): cv.string,
        vol.Optional("since"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)

CREATE_GUEST_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("code"): cv.string,
        vol.Optional("slot"): vol.Coerce(int),
        vol.Optional("until"): cv.string,
        vol.Optional("one_time", default=False): cv.boolean,
        vol.Optional("entry_id"): cv.string,
        vol.Optional("group"): cv.string,
    }
)

WINDOW_SCHEMA = vol.Schema(
    {
        vol.Required("days"): vol.Any(cv.string, [cv.string]),
        vol.Required("start"): cv.string,
        vol.Required("end"): cv.string,
    }
)

CREATE_RECURRING_GUEST_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("schedule"): vol.All(cv.ensure_list, [WINDOW_SCHEMA]),
        vol.Optional("code"): cv.string,
        vol.Optional("slot"): vol.Coerce(int),
        vol.Optional("paused", default=False): cv.boolean,
        vol.Optional("entry_id"): cv.string,
        vol.Optional("group"): cv.string,
    }
)

UPDATE_GUEST_SCHEMA = vol.Schema(
    {
        vol.Required("slot"): vol.Coerce(int),
        vol.Optional("name"): cv.string,
        vol.Optional("code"): cv.string,
        vol.Optional("schedule"): vol.All(cv.ensure_list, [WINDOW_SCHEMA]),
        vol.Optional("paused"): cv.boolean,
        vol.Optional("until"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)

REVOKE_GUEST_SCHEMA = vol.Schema(
    {
        vol.Required("slot"): vol.Coerce(int),
        vol.Optional("entry_id"): cv.string,
    }
)

LIST_GUESTS_SCHEMA = vol.Schema({vol.Optional("entry_id"): cv.string})


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


def _validated(handler: Any) -> Any:
    """Surface a bad request as a service error with the coordinator's message.

    The coordinator raises RuntimeError/ValueError for things the caller can
    fix (a reserved slot, a bad code, an unreachable lock); Home Assistant
    turns ServiceValidationError into a clear 400 instead of a bare 500.
    """

    async def _wrapped(call: ServiceCall) -> Any:
        try:
            return await handler(call)
        except (ValueError, RuntimeError) as err:
            raise ServiceValidationError(str(err)) from err

    return _wrapped


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

    async def _async_handle_fetch_journal(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        coordinators = _coordinators(hass, entry_id, "journal_entries")
        if not coordinators:
            return {"error": "no matching mirror"}
        results: dict[str, Any] = {}
        for coord in coordinators:
            results[coord.entry.entry_id] = coord.journal_entries(
                person=call.data.get("person"),
                slot=call.data.get("slot"),
                action=call.data.get("action"),
                since=call.data.get("since"),
                limit=int(call.data.get("limit") or 50),
            )
        return results

    async def _async_handle_create_guest(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        coordinators = _coordinators(hass, entry_id, "async_create_guest")
        if not coordinators:
            return {"error": "no matching mirror"}
        results: dict[str, Any] = {}
        for coord in coordinators:
            results[coord.entry.entry_id] = await coord.async_create_guest(
                str(call.data["name"]),
                code=call.data.get("code"),
                slot=call.data.get("slot"),
                until=call.data.get("until"),
                one_time=bool(call.data.get("one_time")),
                group=call.data.get("group"),
            )
        return results

    async def _async_handle_create_recurring_guest(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        coordinators = _coordinators(hass, entry_id, "async_create_recurring_guest")
        if not coordinators:
            return {"error": "no matching mirror"}
        results: dict[str, Any] = {}
        for coord in coordinators:
            results[coord.entry.entry_id] = await coord.async_create_recurring_guest(
                str(call.data["name"]),
                code=call.data.get("code"),
                windows=call.data.get("schedule"),
                slot=call.data.get("slot"),
                paused=bool(call.data.get("paused")),
                group=call.data.get("group"),
            )
        return results

    async def _async_handle_update_guest(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        coordinators = _coordinators(hass, entry_id, "async_update_guest")
        if not coordinators:
            return {"error": "no matching mirror"}
        fields = ("name", "code", "schedule", "paused", "until")
        changes = {key: call.data[key] for key in fields if key in call.data}
        results: dict[str, Any] = {}
        for coord in coordinators:
            results[coord.entry.entry_id] = await coord.async_update_guest(
                int(call.data["slot"]), changes
            )
        return results

    async def _async_handle_revoke_guest(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        slot = int(call.data["slot"])
        coordinators = _coordinators(hass, entry_id, "async_revoke_guest")
        if not coordinators:
            return {"error": "no matching mirror"}
        results: dict[str, Any] = {}
        for coord in coordinators:
            results[coord.entry.entry_id] = await coord.async_revoke_guest(slot)
        return results

    async def _async_handle_reset_app(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        coordinators = _coordinators(hass, entry_id, "async_reset_app_registration")
        if not coordinators:
            return {"error": "no matching mirror"}
        results: dict[str, Any] = {}
        for coord in coordinators:
            results[coord.entry.entry_id] = await coord.async_reset_app_registration()
        return results

    async def _async_handle_list_guests(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        coordinators = _coordinators(hass, entry_id, "list_guests")
        if not coordinators:
            return {"error": "no matching mirror"}
        results: dict[str, Any] = {}
        for coord in coordinators:
            results[coord.entry.entry_id] = coord.list_guests()
        return results

    async def _async_handle_link_credential(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        slot = int(call.data["slot"])
        access_type = str(call.data["access_type"])
        user_id = str(call.data["user_id"])
        coordinators = _coordinators(hass, entry_id, "async_set_cloud_link")
        if not coordinators:
            return {"error": "no matching mirror"}
        linked = []
        for coord in coordinators:
            await coord.async_set_cloud_link(slot, access_type, user_id)
            await coord.async_journal_note(
                "cloud_linked", detail=f"slot {slot} {access_type} (manual)"
            )
            linked.append(coord.entry.entry_id)
        return {"linked": linked}

    hass.services.async_register(
        DOMAIN, SERVICE_SET_IEEE, _validated(_async_handle_set_ieee), schema=SET_IEEE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_OTA_INSTALL, _validated(_async_handle_ota_install), schema=OTA_INSTALL_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PROVISION, _validated(_async_handle_provision), schema=PROVISION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SLOT_NAME,
        _validated(_async_handle_set_slot_name),
        schema=SET_SLOT_NAME_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PIN, _validated(_async_handle_set_pin), schema=SET_PIN_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_SLOT, _validated(_async_handle_clear_slot), schema=CLEAR_SLOT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_LOCK_ATTRIBUTES,
        _validated(_async_handle_read_lock_attributes),
        schema=READ_LOCK_ATTRIBUTES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_AUTO_LOCK,
        _validated(_async_handle_set_auto_lock),
        schema=SET_AUTO_LOCK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SOUND_VOLUME,
        _validated(_async_handle_set_sound_volume),
        schema=SET_SOUND_VOLUME_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FETCH_JOURNAL,
        _validated(_async_handle_fetch_journal),
        schema=FETCH_JOURNAL_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_GUEST,
        _validated(_async_handle_create_guest),
        schema=CREATE_GUEST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_RECURRING_GUEST,
        _validated(_async_handle_create_recurring_guest),
        schema=CREATE_RECURRING_GUEST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_GUEST,
        _validated(_async_handle_update_guest),
        schema=UPDATE_GUEST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REVOKE_GUEST,
        _validated(_async_handle_revoke_guest),
        schema=REVOKE_GUEST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_APP,
        _validated(_async_handle_reset_app),
        schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_GUESTS,
        _validated(_async_handle_list_guests),
        schema=LIST_GUESTS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LINK_CREDENTIAL,
        _validated(_async_handle_link_credential),
        schema=LINK_CREDENTIAL_SCHEMA,
        supports_response=SupportsResponse.ONLY,
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
    hass.services.async_remove(DOMAIN, SERVICE_FETCH_JOURNAL)
    hass.services.async_remove(DOMAIN, SERVICE_CREATE_GUEST)
    hass.services.async_remove(DOMAIN, SERVICE_CREATE_RECURRING_GUEST)
    hass.services.async_remove(DOMAIN, SERVICE_UPDATE_GUEST)
    hass.services.async_remove(DOMAIN, SERVICE_REVOKE_GUEST)
    hass.services.async_remove(DOMAIN, SERVICE_RESET_APP)
    hass.services.async_remove(DOMAIN, SERVICE_LIST_GUESTS)
