"""Sensors for the cloud layer.

Everything the vendor knows is exposed as its own entity so a user can enable exactly what
they want. Entities that the vendor rarely or never fills are registered disabled by default;
the device page shows the essentials, and the rest are one switch away.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN, VOLUME_NAMES
from .coordinator import NimlyCloudCoordinator
from .guests import guest_row
from .maintenance import device_name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NimlyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for device in coordinator.devices:
        if not device.get("id"):
            continue
        entities += [
            CloudLockState(coordinator, device),
            CloudLastEvent(coordinator, device),
            CloudLastPerson(coordinator, device),
            CloudLastPinSlot(coordinator, device),
            CloudLastTag(coordinator, device),
            CloudBattery(coordinator, device),
            CloudLqi(coordinator, device),
            CloudSoundVolume(coordinator, device),
            CloudVolumeName(coordinator, device),
            CloudAutorelockTime(coordinator, device),
            CloudFirmware(coordinator, device),
            CloudConfigProgress(coordinator, device),
            CloudRaw(coordinator, device),
        ]

    entities += [
        CloudRemainingPinAttempts(coordinator),
        CloudAlarmState(coordinator),
        CloudDeviceCount(coordinator),
        CloudUserCount(coordinator),
        CloudGuests(coordinator),
    ]

    for user in coordinator.users:
        if user.get("id"):
            entities.append(CloudUserAccess(coordinator, user))

    async_add_entities(entities)


class _CloudEntity(CoordinatorEntity[NimlyCloudCoordinator]):
    """An entity on a lock's device, merged with the local device when there is one."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NimlyCloudCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id = device.get("id")
        # Entity identity is the module serial, not the vendor device id: a
        # re-registration of the same lock must reuse these entities.
        self._identity = coordinator.device_serial(self._device_id) or self._device_id

    @property
    def device_info(self) -> DeviceInfo:
        # One config entry per device (Home Assistant 2026): the cloud layer keeps
        # its own device, identified by the vendor device id, and named after the
        # lock — the user's name for the device that owns the serial (ZHA,
        # typically) wins over the vendor's default.
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_name(self.coordinator, self._device_id)
            or self._device.get("name"),
            manufacturer=self._device.get("modelVendor"),
            model=self._device.get("modelName"),
        )


class _DeviceSensor(_CloudEntity, SensorEntity):
    """A sensor reading one field of the vendor's device state."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: NimlyCloudCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._identity}_{self.translation_key}"


class CloudLockState(_DeviceSensor):
    _attr_translation_key = "lock_state"
    _attr_entity_registry_enabled_default = True

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.device_state(self._device_id, "lock", "state")
        if value is None:
            return None
        return "locked" if value else "unlocked"

    @property
    def icon(self) -> str:
        value = self.coordinator.device_state(self._device_id, "lock", "state")
        return "mdi:lock" if value else "mdi:lock-open-variant"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "vendor_time": self.coordinator.device_stamp(self._device_id, "lock", "state"),
        }


class CloudLastEvent(_DeviceSensor):
    _attr_translation_key = "last_event"
    _attr_entity_registry_enabled_default = True
    _attr_icon = "mdi:lock-clock"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.device_last_event(self._device_id).get("value")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        event = self.coordinator.last_events.get(self._device_id) or {}
        return {
            "action": event.get("action"),
            "source": event.get("source"),
            "slot": event.get("slot"),
            "user_id": event.get("user_id"),
            "user_name": event.get("user_name"),
            "vendor_time": event.get("vendor_time")
            or self.coordinator.device_last_event(self._device_id).get("lastUpdated"),
            "observed_at": event.get("observed_at"),
        }


class CloudLastPerson(_DeviceSensor):
    """The person the cloud attributed the most recent activity to.

    Reads the last *named* activity, so an unattributed event (auto-lock, a mirrored
    action) does not clear who last opened the door.
    """

    _attr_translation_key = "last_person"
    _attr_entity_registry_enabled_default = True
    _attr_icon = "mdi:account-key"

    @property
    def native_value(self) -> str | None:
        person = self.coordinator.last_persons.get(self._device_id) or {}
        return person.get("user_name")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        person = self.coordinator.last_persons.get(self._device_id) or {}
        return {
            "user_id": person.get("user_id"),
            "action": person.get("action"),
            "source": person.get("source"),
            "vendor_time": person.get("vendor_time"),
            "observed_at": person.get("observed_at"),
        }


class CloudLastPinSlot(_DeviceSensor):
    _attr_translation_key = "last_pin_slot"
    _attr_icon = "mdi:dialpad"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.device_state(self._device_id, "pins", "LastPinUsed")
        return None if value is None else str(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"vendor_time": self.coordinator.device_stamp(self._device_id, "pins", "LastPinUsed")}


class CloudLastTag(_DeviceSensor):
    _attr_translation_key = "last_tag"
    _attr_icon = "mdi:nfc"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.device_state(self._device_id, "tags", "LastTagScanned")
        return None if value is None else str(value)


class CloudBattery(_DeviceSensor):
    _attr_translation_key = "battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> int | None:
        return _int_or_none(
            self.coordinator.device_state(self._device_id, "battery", "percentage")
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        voltage = self.coordinator.device_state(self._device_id, "battery", "voltage")
        return {"voltage": voltage} if voltage is not None else {}


class CloudLqi(_DeviceSensor):
    _attr_translation_key = "lqi"

    @property
    def native_value(self) -> int | None:
        return _int_or_none(
            self.coordinator.device_state(self._device_id, "diagnostic", "lqi")
        )


class CloudSoundVolume(_DeviceSensor):
    _attr_translation_key = "sound_volume"

    @property
    def native_value(self) -> int | None:
        return _int_or_none(
            self.coordinator.device_state(self._device_id, "lock", "soundvolume")
        )


class CloudVolumeName(_DeviceSensor):
    _attr_translation_key = "volume_name"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.device_setting(self._device_id, "volume")
        return str(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        volume = _int_or_none(
            self.coordinator.device_state(self._device_id, "lock", "soundvolume")
        )
        return {"vendor_levels": VOLUME_NAMES, "raw": volume}


class CloudAutorelockTime(_DeviceSensor):
    _attr_translation_key = "autorelock_time"

    @property
    def native_value(self) -> int | None:
        return _int_or_none(
            self.coordinator.device_state(self._device_id, "lock", "autorelocktime")
        )


class CloudFirmware(_DeviceSensor):
    _attr_translation_key = "firmware"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.device_state(self._device_id, "lock", "hex_version")
        return str(value) if value is not None else None


class CloudConfigProgress(_DeviceSensor):
    _attr_translation_key = "config_progress"

    @property
    def native_value(self) -> int | None:
        return _int_or_none(self.coordinator.device_meta_value(self._device_id, "configProgressPercentage"))


class CloudRaw(_DeviceSensor):
    """Everything the vendor returns for this lock, credentials masked."""

    _attr_translation_key = "raw"
    _attr_icon = "mdi:code-json"

    @property
    def native_value(self) -> str | None:
        features = self.coordinator.device_raw(self._device_id).get("features") or {}
        names = [name for name, feature in features.items() if (feature or {}).get("states")]
        return ",".join(sorted(names)) or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.device_raw(self._device_id)


class _LocationEntity(CoordinatorEntity[NimlyCloudCoordinator]):
    """Something the account or location owns, rather than a single lock."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: NimlyCloudCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"location_{self.translation_key}"

    @property
    def device_info(self) -> DeviceInfo:
        location = self.coordinator.location or {}
        return DeviceInfo(
            identifiers={(DOMAIN, str(location.get("locationId") or "location"))},
            name=f"Nimly Cloud ({location.get('name') or 'account'})",
            manufacturer="Onesti Products AS",
            model=str(location.get("locationType") or ""),
        )


class _LocationSensor(_LocationEntity, SensorEntity):
    """A sensor on the account device."""


class CloudRemainingPinAttempts(_LocationSensor):
    _attr_translation_key = "remaining_pin_attempts"
    _attr_entity_registry_enabled_default = True
    _attr_icon = "mdi:shield-key-outline"

    @property
    def native_value(self) -> int | None:
        return _int_or_none(self.coordinator.location_value("remainingPinAttempts"))


class CloudAlarmState(_LocationSensor):
    _attr_translation_key = "alarm_state"
    _attr_entity_registry_enabled_default = True
    _attr_icon = "mdi:shield-home"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.location_value("alarmState")
        return None if value is None else str(value)


class CloudDeviceCount(_LocationSensor):
    _attr_translation_key = "device_count"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.devices)


class CloudUserCount(_LocationSensor):
    _attr_translation_key = "user_count"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.users)


class CloudGuests(_LocationSensor):
    """The account's guest users, as the app lists them."""

    _attr_translation_key = "guests"
    _attr_entity_registry_enabled_default = True
    _attr_icon = "mdi:account-multiple"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.guest_users)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "guests": [
                guest_row(guest)
                for guest in self.coordinator.guest_users
                if guest.get("id")
            ]
        }


class CloudUserAccess(CoordinatorEntity[NimlyCloudCoordinator], SensorEntity):
    """One sensor per person: what they may use, on which door."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-key"

    def __init__(self, coordinator: NimlyCloudCoordinator, user: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._user = user
        self._user_id = str(user.get("id"))
        self._attr_translation_key = "user_access"
        self._attr_translation_placeholders = {
            "name": str(user.get("name") or user.get("firstName") or self._user_id)
        }
        self._attr_unique_id = f"user_{self._user_id}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "users")},
            name="Nimly Cloud users",
            manufacturer="Onesti Products AS",
        )

    @property
    def native_value(self) -> str | None:
        return self._user.get("name") or self._user.get("firstName")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        matrix: dict[str, list[str]] = {}
        for device in self.coordinator.devices:
            device_id = device.get("id")
            if not device_id:
                continue
            types = [
                str(grant.get("type"))
                for grant in self.coordinator.device_access(device_id)
                if str(grant.get("userId")) == self._user_id and grant.get("type")
            ]
            if types:
                matrix[str(device.get("name") or device_id)] = sorted(types)

        return {
            "user_id": self._user_id,
            "email": self._user.get("email"),
            "role": self._user.get("role"),
            "has_pin": self._user.get("hasDoorlockPin"),
            "has_fingerprint": self._user.get("hasDoorlockFingerprint"),
            "has_tag": self._user.get("hasDoorlockTag"),
            "has_access": self._user.get("hasDoorlockAccess"),
            "access": matrix,
        }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
