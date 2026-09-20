"""Services for the cloud layer.

The write services are deliberately explicit rather than entities: the local mirror layer
is the primary way to control a lock, and the cloud is an optional second one.
Making them services keeps that choice visible in the automation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import NimlyCloudError
from ..const import (
    DOMAIN,
    SERVICE_FETCH_HISTORY,
    SERVICE_GATEWAY_SCAN,
    SERVICE_PROBE,
    SERVICE_REFRESH,
    SERVICE_SET_LOCK,
)
from .coordinator import NimlyCloudCoordinator

_LOGGER = logging.getLogger(__name__)

PROBE_MAX = 25

# How long to wait before re-reading the lock after a timed-out acknowledgement.
CONFIRM_DELAY = 8

SCHEMA_REFRESH = vol.Schema({vol.Optional("entry_id"): cv.string})

SCHEMA_FETCH_HISTORY = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=250)),
    }
)

SCHEMA_SET_LOCK = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("locked"): cv.boolean,
    }
)

SCHEMA_GATEWAY_SCAN = vol.Schema({vol.Required("start"): cv.boolean})

SCHEMA_PROBE = vol.Schema({vol.Required("paths"): vol.All(cv.ensure_list, [vol.Any(str, dict)])})


def _coordinator(hass: HomeAssistant, entry_id: str | None = None) -> NimlyCloudCoordinator:
    entries = {
        key: coordinator
        for key, coordinator in hass.data.get(DOMAIN, {}).items()
        if isinstance(coordinator, NimlyCloudCoordinator)
    }
    if not entries:
        raise HomeAssistantError("the Nimly cloud account is not configured")
    if entry_id and entry_id in entries:
        return entries[entry_id]
    return next(iter(entries.values()))


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register every service once, at integration setup."""

    async def _refresh(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call.data.get("entry_id"))
        await coordinator.async_request_refresh()
        return {"ok": True}

    async def _fetch_history(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        limit: int = call.data["limit"]
        device_id = call.data.get("device_id")
        events = [
            event
            for event in coordinator.events
            if device_id is None or event.get("device_id") == device_id
        ]
        return {"events": events[:limit]}

    async def _set_lock(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        device_id: str = call.data["device_id"]
        locked: bool = call.data["locked"]
        try:
            response = await coordinator.api.async_set_lock(device_id, locked)
        except NimlyCloudError as err:
            if "timeout" not in str(err).lower():
                raise HomeAssistantError(f"the cloud rejected the request: {err}") from err
            # The vendor's acknowledgement can time out while the command still reaches
            # the lock. Wait, re-read the state, and report what actually happened
            # instead of failing an automation on a missing receipt.
            await asyncio.sleep(CONFIRM_DELAY)
            await coordinator.async_request_refresh()
            actual = coordinator.device_state(device_id, "lock", "state")
            if actual is not None and bool(actual) == locked:
                return {"ok": True, "confirmed": False, "message": str(err)}
            raise HomeAssistantError(
                f"the cloud accepted the request but the lock did not confirm it: {err}"
            ) from err
        await coordinator.async_request_refresh()
        return {"ok": True, "confirmed": True, "response": response}

    async def _gateway_scan(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        gateway_id = coordinator.gateway().get("id")
        if not gateway_id:
            raise HomeAssistantError("the account has no gateway")
        try:
            response = await coordinator.api.async_gateway_scan(
                str(gateway_id), bool(call.data["start"])
            )
        except NimlyCloudError as err:
            raise HomeAssistantError(f"the cloud rejected the request: {err}") from err
        return {"ok": True, "response": response}

    async def _probe(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        results = []
        for spec in call.data["paths"][:PROBE_MAX]:
            if isinstance(spec, str) and spec.startswith("/"):
                results.append(await coordinator.api.async_probe(spec))
            elif (
                isinstance(spec, dict)
                and isinstance(spec.get("path"), str)
                and spec["path"].startswith("/")
            ):
                results.append(
                    await coordinator.api.async_probe(
                        spec["path"],
                        spec.get("method", "GET"),
                        spec.get("json"),
                        spec.get("headers"),
                    )
                )
        return {"results": results}

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, _refresh, schema=SCHEMA_REFRESH,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FETCH_HISTORY, _fetch_history, schema=SCHEMA_FETCH_HISTORY,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_LOCK, _set_lock, schema=SCHEMA_SET_LOCK,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GATEWAY_SCAN, _gateway_scan, schema=SCHEMA_GATEWAY_SCAN,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PROBE, _probe, schema=SCHEMA_PROBE,
        supports_response=SupportsResponse.ONLY,
    )
