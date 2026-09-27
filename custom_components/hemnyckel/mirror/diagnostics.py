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
        "related_entities": dict(coordinator.related),
        "lock_facts": dict(coordinator.lock_facts),
        "lock_facts_at": coordinator.lock_facts_at,
        "journal_summary": coordinator.journal_summary(),
        "journal_recent": list(coordinator.journal[-5:]),
        "guests": coordinator.list_guests(),
        "counters": dict(coordinator.counters),
        "slots": coordinator.slots.snapshot(),
        "zha_listener": bool(
            coordinator.zha is not None and coordinator.zha.attached
        ),
    }
