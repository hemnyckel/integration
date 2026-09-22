"""Firmware update entities: the emulator (over the bridge's UART ferry) and the
bridge itself (OTA over MQTT).

Both report live progress to Home Assistant. The bridge publishes a percentage
while it downloads or ferries an image; the entity forwards it through the
update entity's progress API and the install call waits for the outcome, so the
UI shows "Installing" with a progress bar instead of silently returning while
the update runs in the background.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import CONF_TYPE, DOMAIN, TYPE_BRIDGE
from .bridge import BridgeEntity
from .coordinator import MirrorCoordinator
from .entity import MirrorEntity

_LOGGER = logging.getLogger(__name__)

# Progress reporting exists since Home Assistant 2025.1; look the flag up so a
# slightly older core still loads the integration.
_PROGRESS = getattr(UpdateEntityFeature, "PROGRESS", UpdateEntityFeature(0))
_INSTALL_FEATURES = UpdateEntityFeature.INSTALL | _PROGRESS

# The C6 ferry takes about three minutes; give a slow download room.
_WAIT_TIMEOUT = 900
_POLL_SECONDS = 2.0
_TERMINAL_DONE = "done"
_TERMINAL_ERROR = "error"


def _fraction(status: dict[str, Any]) -> float | None:
    """The status' percentage as a 0-1 fraction (never quite 1 while running)."""
    pct = status.get("pct")
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        return None
    return min(max(float(pct) / 100.0, 0.0), 0.999)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_TYPE) == TYPE_BRIDGE:
        async_add_entities([BridgeUpdate(coordinator)])
        return
    async_add_entities([MirrorEmulatorUpdate(coordinator), MirrorBridgeUpdate(coordinator)])


class MirrorEmulatorUpdate(MirrorEntity, UpdateEntity):
    """The emulator (C6) firmware, installed through the bridge's UART ferry."""

    _attr_name = "Emulator firmware"
    _attr_title = "Nimly emulator"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = _INSTALL_FEATURES

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_emulator_update"

    def _manifest_entry(self) -> dict[str, Any]:
        manifest = self.coordinator.firmware_manifest
        emulator = manifest.get("emulator") if isinstance(manifest, dict) else None
        return emulator if isinstance(emulator, dict) else {}

    def _ota_status(self) -> dict[str, Any]:
        """The ferry's status for the C6 (the bridge's own OTA has no target)."""
        status = self.coordinator.ota_status or {}
        if status.get("target") not in (None, "c6"):
            return {}
        return status

    @property
    def installed_version(self) -> str | None:
        return self.coordinator.firmware

    @property
    def latest_version(self) -> str | None:
        value = self._manifest_entry().get("version")
        return value if isinstance(value, str) else self.installed_version

    @property
    def in_progress(self) -> bool:
        return self._ota_status().get("state") in ("downloading", "flashing")

    @property
    def release_url(self) -> str | None:
        return self.coordinator.ota_manifest_url

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"ota_status": self.coordinator.ota_status or {}}

    @callback
    def _handle_coordinator_update(self) -> None:
        fraction = _fraction(self._ota_status())
        if fraction is not None and self.in_progress:
            self.async_update_progress(fraction)
        super()._handle_coordinator_update()

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        entry = self._manifest_entry()
        name = entry.get("file")
        sha = entry.get("sha256")
        if not isinstance(name, str) or not isinstance(sha, str):
            raise HomeAssistantError("The OTA manifest has no emulator image")
        base = self.coordinator.ota_manifest_url.rsplit("/", 1)[0]
        await self.coordinator.async_ota_c6(
            f"{base}/{name}", sha, str(entry.get("version") or version or "")
        )
        await _wait_for_ota(self, self._ota_status)


class MirrorBridgeUpdate(MirrorEntity, UpdateEntity):
    """The bridge firmware, seen from the mirror entry."""

    _attr_name = "Bridge firmware"
    _attr_title = "Nimly Bridge"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = _INSTALL_FEATURES

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_bridge_update"

    def _ota_status(self) -> dict[str, Any]:
        """The bridge's own OTA: its payloads carry no target."""
        status = self.coordinator.ota_status or {}
        if status.get("target") not in (None, "bridge"):
            return {}
        return status

    @property
    def installed_version(self) -> str | None:
        value = self.coordinator.bridge_info.get("fw")
        return value if isinstance(value, str) else None

    @property
    def latest_version(self) -> str | None:
        manifest = self.coordinator.firmware_manifest
        if isinstance(manifest, dict) and isinstance(manifest.get("version"), str):
            return manifest["version"]
        return self.installed_version

    @property
    def in_progress(self) -> bool:
        return self._ota_status().get("state") in ("downloading", "flashing")

    @property
    def release_url(self) -> str | None:
        return self.coordinator.ota_manifest_url

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"ota_status": self.coordinator.ota_status or {}}

    @callback
    def _handle_coordinator_update(self) -> None:
        fraction = _fraction(self._ota_status())
        if fraction is not None and self.in_progress:
            self.async_update_progress(fraction)
        super()._handle_coordinator_update()

    def _binary_url(self) -> str | None:
        manifest = self.coordinator.firmware_manifest or {}
        builds = manifest.get("builds") or {}
        target = self.coordinator.bridge_info.get("target")
        name = builds.get(target)
        if not isinstance(name, str):
            return None
        base = self.coordinator.ota_manifest_url.rsplit("/", 1)[0]
        return f"{base}/{name}"

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        url = self._binary_url()
        if not url:
            raise HomeAssistantError("The OTA manifest has no binary for the bridge target")
        await self.coordinator.async_ota(url)
        await _wait_for_ota(self, self._ota_status)


class BridgeUpdate(BridgeEntity, UpdateEntity):
    """The bridge firmware, seen from its own entry."""

    _attr_name = "Bridge firmware"
    _attr_title = "Nimly Bridge"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = _INSTALL_FEATURES

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"bridge_{coordinator.address}_update"

    def _ota_status(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("ota") or {}

    @property
    def installed_version(self) -> str | None:
        value = ((self.coordinator.data or {}).get("info") or {}).get("fw")
        return value if isinstance(value, str) else None

    @property
    def latest_version(self) -> str | None:
        manifest = (self.coordinator.data or {}).get("manifest") or {}
        value = manifest.get("version")
        return value if isinstance(value, str) else self.installed_version

    @property
    def in_progress(self) -> bool:
        return self._ota_status().get("state") in ("downloading", "flashing")

    @property
    def release_url(self) -> str | None:
        return self.coordinator.ota_manifest_url

    @callback
    def _handle_coordinator_update(self) -> None:
        fraction = _fraction(self._ota_status())
        if fraction is not None and self.in_progress:
            self.async_update_progress(fraction)
        super()._handle_coordinator_update()

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        url = self.coordinator.binary_url()
        if not url:
            raise HomeAssistantError("The OTA manifest has no binary for the bridge target")
        await self.coordinator.async_ota(url)
        await _wait_for_ota(self, self._ota_status)


async def _wait_for_ota(entity: Any, status_getter: Any) -> None:
    """Block the install call until the firmware reports done (or fails).

    Home Assistant shows the entity as installing for as long as the call runs,
    so this is also what gives the UI its spinner; the percentage comes from the
    bridge's own status messages through the progress API.
    """
    deadline = time.monotonic() + _WAIT_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_SECONDS)
        status = status_getter()
        state = status.get("state")
        fraction = _fraction(status)
        if fraction is not None and hasattr(entity, "async_update_progress"):
            entity.async_update_progress(fraction)
        if state == _TERMINAL_DONE:
            if hasattr(entity, "async_update_progress"):
                entity.async_update_progress(1.0)
            return
        if state == _TERMINAL_ERROR:
            raise HomeAssistantError(
                f"The firmware update failed: {status.get('msg') or 'unknown error'}"
            )
    # Not an error: the update may still finish; the entity keeps showing
    # in_progress until the status changes, which also guards the install button.
    _LOGGER.warning(
        "The firmware update did not report completion within %s seconds", _WAIT_TIMEOUT
    )
