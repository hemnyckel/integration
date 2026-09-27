"""Shared base for mirror-layer entities."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from .coordinator import MirrorCoordinator


class MirrorEntity(CoordinatorEntity[MirrorCoordinator]):
    """An entity attached to the mirror coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        # Name the device after the lock itself, which is what a household
        # recognises ("Ytterdörren"); the entry title is the fallback for a
        # lock that has no state yet. With the mirror's own lock entity gone
        # there is no longer a collision with ZHA, so the entity ids read
        # "sensor.ytterdorren_journal", not "sensor.nimly_ytterdorren_journal".
        lock_state = coordinator.hass.states.get(coordinator.lock_entity_id)
        lock_name = None
        if lock_state is not None:
            lock_name = lock_state.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=lock_name or coordinator.entry.title,
            manufacturer="nimly",
            model="Nimly Connect Module",
            configuration_url="https://github.com/hemnyckel/integration",
        )

    def door_identity(self) -> dict[str, Any]:
        """The two keys that name this door wherever its sensors appear.

        ``entry_id`` says which config entry - which door - a value belongs to,
        and ``lock`` is the name the household uses for it ("Ytterdörren"), so a
        consumer can tell two doors' sensors apart without knowing any entity
        id. Every door-scoped sensor carries them.
        """
        lock_state = self.coordinator.hass.states.get(self.coordinator.lock_entity_id)
        return {
            "entry_id": self.coordinator.entry.entry_id,
            "lock": (
                lock_state.name
                if lock_state is not None
                else self.coordinator.entry.title
            ),
        }
