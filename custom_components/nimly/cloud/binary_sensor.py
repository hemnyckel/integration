"""Binary sensors for the cloud layer: presence and the vendor's device settings."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import DOMAIN, SETTING_AUTOLOCK, SETTING_MASTER_PIN_MODE
from ..const import SETTING_PART_OF_ALARM, SETTING_PIN_REQUIRED_REMOTE
from .coordinator import NimlyCloudCoordinator
from .sensor import _CloudEntity, _LocationEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NimlyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []

    for device in coordinator.devices:
        if not device.get("id"):
            continue
        entities += [
            CloudOnline(coordinator, device),
            CloudAutolock(coordinator, device),
            CloudMasterPinMode(coordinator, device),
            CloudPinRequiredRemote(coordinator, device),
            CloudPartOfAlarm(coordinator, device),
        ]

    entities.append(CloudGatewayOnline(coordinator))
    async_add_entities(entities)


class CloudOnline(_CloudEntity, BinarySensorEntity):
    """Whether the vendor cloud currently reaches the lock."""

    _attr_has_entity_name = True
    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: NimlyCloudCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._device_id}_online"

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.device_meta_value(self._device_id, "online")
        return None if value is None else bool(value)


class _DeviceSetting(_CloudEntity, BinarySensorEntity):
    """A boolean the vendor stores in the device's settings block."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _setting_key: str = ""

    def __init__(self, coordinator: NimlyCloudCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._device_id}_{self.translation_key}"

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.device_setting(self._device_id, self._setting_key)
        return None if value is None else bool(value)


class CloudAutolock(_DeviceSetting):
    _attr_translation_key = "autolock"
    _setting_key = SETTING_AUTOLOCK


class CloudMasterPinMode(_DeviceSetting):
    _attr_translation_key = "master_pin_mode"
    _setting_key = SETTING_MASTER_PIN_MODE


class CloudPinRequiredRemote(_DeviceSetting):
    _attr_translation_key = "pin_required_remote"
    _setting_key = SETTING_PIN_REQUIRED_REMOTE


class CloudPartOfAlarm(_DeviceSetting):
    _attr_translation_key = "part_of_alarm"
    _setting_key = SETTING_PART_OF_ALARM


class _LocationBinarySensor(_LocationEntity, BinarySensorEntity):
    """A binary sensor on the account device."""


class CloudGatewayOnline(_LocationBinarySensor):
    """Whether the vendor's bridge is online."""

    _attr_translation_key = "gateway_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_registry_enabled_default = True

    @property
    def is_on(self) -> bool | None:
        gateway = self.coordinator.gateway()
        value = gateway.get("online")
        return None if value is None else bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        gateway = self.coordinator.gateway()
        return {
            "name": gateway.get("name"),
            "model": gateway.get("modelName"),
            "model_number": gateway.get("modelNumber"),
            "serial_number": gateway.get("serialNumber"),
            "online_updated_at": gateway.get("onlineUpdatedAt"),
        }
