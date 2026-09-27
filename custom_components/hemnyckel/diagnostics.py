"""Diagnostics for hemnyckel."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_TYPE, TYPE_MIRROR


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    if entry.data.get(CONF_TYPE) == TYPE_MIRROR:
        from .mirror.diagnostics import (
            async_get_config_entry_diagnostics as mirror_diagnostics,
        )

        return await mirror_diagnostics(hass, entry)
    return {}
