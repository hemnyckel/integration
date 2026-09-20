"""Bridge firmware updates over OTA (the bridge downloads and reboots)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import CONF_TYPE, DOMAIN, TYPE_BRIDGE
from .bridge import BridgeEntity
from .coordinator import MirrorCoordinator
from .entity import MirrorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_TYPE) == TYPE_BRIDGE:
        async_add_entities([BridgeUpdate(coordinator)])
        return
    async_add_entities([MirrorBridgeUpdate(coordinator)])


class MirrorBridgeUpdate(MirrorEntity, UpdateEntity):
    """The bridge firmware. The install is done with OTA over MQTT."""

    _attr_name = "Bridge firmware"
    _attr_title = "Nimly Bridge"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: MirrorCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_bridge_update"

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
        status = self.coordinator.ota_status or {}
        return status.get("state") == "downloading"

    @property
    def release_url(self) -> str | None:
        return self.coordinator.ota_manifest_url

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"ota_status": self.coordinator.ota_status or {}}

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
            raise ValueError("The OTA manifest has no binary for the bridge target")
        await self.coordinator.async_ota(url)


class BridgeUpdate(BridgeEntity, UpdateEntity):
    """The bridge firmware - installed with OTA over MQTT."""

    _attr_name = "Bridge firmware"
    _attr_title = "Nimly Bridge"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"bridge_{coordinator.address}_update"

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
        return ((self.coordinator.data or {}).get("ota") or {}).get("state") == "downloading"

    @property
    def release_url(self) -> str | None:
        return self.coordinator.ota_manifest_url

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        url = self.coordinator.binary_url()
        if not url:
            raise ValueError("The OTA manifest has no binary for the bridge target")
        await self.coordinator.async_ota(url)
