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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer="nimly-tools",
            model="Nimly Connect Bridge-emulator",
            configuration_url="https://github.com/c14ym0re/nimly-tools",
        )
