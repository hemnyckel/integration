"""Diagnostics for nimly — routes each config entry to its layer."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_TYPE, DOMAIN, TYPE_BRIDGE, TYPE_CLOUD, TYPE_MIRROR


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    entry_type = entry.data.get(CONF_TYPE)
    if entry_type == TYPE_CLOUD:
        from .cloud.diagnostics import (
            async_get_config_entry_diagnostics as cloud_diagnostics,
        )

        return await cloud_diagnostics(hass, entry)
    if entry_type == TYPE_MIRROR:
        from .mirror.diagnostics import (
            async_get_config_entry_diagnostics as mirror_diagnostics,
        )

        return await mirror_diagnostics(hass, entry)
    if entry_type == TYPE_BRIDGE:
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        info = dict(getattr(coordinator, "info", {}) or {})
        return {"entry_type": TYPE_BRIDGE, "info": info}
    return {}
