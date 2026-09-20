"""Diagnostics for the cloud layer — everything except the tokens."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..const import CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, DOMAIN
from .coordinator import NimlyCloudCoordinator

_REDACT = {CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: NimlyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": {
            "title": entry.title,
            "data": {
                key: "**REDACTED**" if key in _REDACT else value
                for key, value in entry.data.items()
            },
            "options": dict(entry.options or {}),
        },
        "account": coordinator.account_raw(),
        "clock_skew_seconds": coordinator.clock_skew_seconds,
        "devices": {
            device_id: coordinator.device_raw(device_id)
            for device_id in coordinator.states
        },
        "recent_events": coordinator.events[:20],
        "known_entries": len(coordinator._seen_entries),  # noqa: SLF001 - diagnostics
    }
