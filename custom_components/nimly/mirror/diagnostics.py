"""Diagnostics for the mirror layer (no secrets - PIN codes are never logged)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from .coordinator import MirrorCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: MirrorCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "lock_entity_id": coordinator.lock_entity_id,
        "ieee": coordinator.ieee,
        "endpoint_id": coordinator.endpoint_id,
        "topic_prefix": coordinator.prefix,
        "master_enabled": coordinator.master_enabled,
        "channels": dict(coordinator.channels),
        "related_entities": dict(coordinator.related),
        "bridge_online": coordinator.bridge_online,
        "emulator_joined": coordinator.emulator_joined,
        "app_locked": coordinator.app_locked,
        "app_battery": coordinator.app_battery,
        "app_volume": coordinator.app_volume,
        "app_autolock": coordinator.app_autolock,
        "lock_facts": dict(coordinator.lock_facts),
        "lock_facts_at": coordinator.lock_facts_at,
        "settings_drift": dict(coordinator.settings_drift),
        "journal_summary": coordinator.journal_summary(),
        "journal_recent": list(coordinator.journal[-5:]),
        "guests": coordinator.list_guests(),
        "counters": dict(coordinator.counters),
        "last_error": coordinator.last_error,
        "slots": coordinator.slots.snapshot(),
        "slot_map": {str(k): v for k, v in coordinator.slot_map.items()},
        "zha_listener": bool(
            coordinator.zha is not None and coordinator.zha.attached
        ),
    }
