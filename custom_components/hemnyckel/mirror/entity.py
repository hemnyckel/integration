"""Shared base for mirror-layer entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from .coordinator import MirrorCoordinator


class MirrorEntity(CoordinatorEntity[MirrorCoordinator]):
    """An entity attached to the mirror coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        # Name the device after the lock it mirrors, which is what a household
        # recognises ("Nimly (Front door)"); the entry title is the fallback for
        # a lock that has no state yet.
        lock_state = coordinator.hass.states.get(coordinator.lock_entity_id)
        lock_name = None
        if lock_state is not None:
            lock_name = lock_state.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=f"Nimly ({lock_name})" if lock_name else coordinator.entry.title,
            manufacturer="nimly",
            model="Nimly Connect Module (emulated)",
            configuration_url="https://github.com/hemnyckel/integration",
        )
