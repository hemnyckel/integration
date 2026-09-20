"""Diagnostics for nimly — routes each config entry to its layer."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_TYPE, TYPE_CLOUD


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    if entry.data.get(CONF_TYPE) == TYPE_CLOUD:
        from .cloud.diagnostics import (
            async_get_config_entry_diagnostics as cloud_diagnostics,
        )

        return await cloud_diagnostics(hass, entry)
    return {}
