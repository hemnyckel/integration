"""Registry upkeep for the cloud layer.

Two jobs, run at every cloud setup and on demand from the ``cleanup_cloud``
service:

- The same physical lock gets a new vendor device id whenever the app registers
  it again. Entity identity is keyed on the module serial (see identity.py), so a
  re-registration reuses the existing entities instead of creating ``_2`` twins;
  entries left on the old, vendor-id scheme are migrated onto it.
- What a removed device left behind is removed: entities that no longer describe
  any device in the account, and the device entry of a vendor device id the
  account no longer lists.

Each config entry owns its own device (Home Assistant 2026.9 moved to one config
entry per device), so the cloud entities stay on the cloud entry's device; only
their identity and name follow the lock.

The lock's proper name is resolved before entities are made: the user's name for
the device that already owns the serial (the ZHA device, typically) wins over the
vendor's default, and is cached in the entry options so a later re-registration
keeps it.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN
from .coordinator import NimlyCloudCoordinator
from .identity import (
    collision_rank,
    is_vendor_uuid,
    normalise_serial,
    serial_from_identifiers,
    split_unique_id,
)

_LOGGER = logging.getLogger(__name__)

OPTION_LOCK_NAMES = "lock_names"


def _preferred_device(
    dev_reg: dr.DeviceRegistry, serial: str, own_entry_id: str
) -> dr.DeviceEntry | None:
    """The registry device that should own the lock's cloud entities.

    Preference: a device the user has named, then a device another integration
    created (ZHA registers the module as a Zigbee device), then anything.
    """
    matches: list[dr.DeviceEntry] = []
    for device in dev_reg.devices:
        for identifier in device.identifiers:
            if serial_from_identifiers([identifier]) == serial:
                matches.append(device)
                break
    if not matches:
        return None

    def rank(device: dr.DeviceEntry) -> tuple[bool, bool]:
        named = not (device.name_by_user or "").strip()
        foreign = not any(entry != own_entry_id for entry in device.config_entries)
        return (named, foreign)

    return min(matches, key=rank)


def _best_name(device: dr.DeviceEntry | None) -> str | None:
    """The most human name a device carries, if any."""
    if device is None:
        return None
    for candidate in (device.name_by_user, device.name):
        text = (candidate or "").strip()
        if text:
            return text
    return None


def lock_names(entry: ConfigEntry) -> dict[str, str]:
    """The serial -> name cache from the entry options."""
    stored = entry.options.get(OPTION_LOCK_NAMES)
    if not isinstance(stored, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in stored.items()
        if normalise_serial(key) and str(value).strip()
    }


def device_name(coordinator: NimlyCloudCoordinator, device_id: str | None) -> str | None:
    """The best name known for the lock, ahead of the vendor's default.

    The user's name on the device that already owns the module serial (the ZHA
    device, typically) wins; then a name remembered in the entry options. None
    means the caller should fall back to the vendor's own device name.
    """
    serial = coordinator.device_serial(device_id)
    if not serial:
        return None
    preferred = _preferred_device(
        dr.async_get(coordinator.hass), serial, coordinator.entry.entry_id
    )
    name = _best_name(preferred)
    if name:
        return name
    return lock_names(coordinator.entry).get(serial)


async def async_reconcile(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: NimlyCloudCoordinator,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Align the registry with the account: migrate, prune, remember names."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    device_serials: dict[str, str] = {}
    for device in coordinator.devices:
        device_id = device.get("id")
        serial = coordinator.device_serial(device_id)
        if device_id and serial:
            device_serials[device_id] = serial
    serials = set(device_serials.values())

    report: dict[str, Any] = {
        "dry_run": dry_run,
        "migrated": [],
        "removed_entities": [],
        "removed_devices": [],
        "names": {},
    }
    _LOGGER.info(
        "Cloud registry reconcile: %s device(s), %s serial(s)",
        len(device_serials),
        len(serials),
    )

    entities = list(er.async_entries_for_config_entry(ent_reg, entry.entry_id))
    claimed = set()
    for entity in entities:
        parsed = split_unique_id(entity.unique_id)
        if parsed and parsed[0] in serials:
            claimed.add(entity.unique_id)

    for entity in sorted(entities, key=lambda item: collision_rank(item.entity_id)):
        parsed = split_unique_id(entity.unique_id)
        if parsed is None:
            continue
        identity, key = parsed
        if not (is_vendor_uuid(identity) or normalise_serial(identity)):
            continue  # an account-level entity (location, user), not a lock's
        if identity in serials:
            continue
        serial = device_serials.get(identity)
        if serial is None:
            device = dev_reg.async_get(entity.device_id) if entity.device_id else None
            serial = serial_from_identifiers(device.identifiers) if device else None
        target = f"{serial}_{key}" if serial and serial in serials else None
        if serial and target and target not in claimed:
            # The entity keeps its id and its device; only the identity moves onto
            # the serial, which is what makes the next re-registration reuse it.
            if not dry_run:
                ent_reg.async_update_entity(entity.entity_id, new_unique_id=target)
            claimed.add(target)
            report["migrated"].append(
                {"entity_id": entity.entity_id, "unique_id": target}
            )
            continue
        if not dry_run:
            ent_reg.async_remove(entity.entity_id)
        report["removed_entities"].append(entity.entity_id)

    for device in list(dr.async_entries_for_config_entry(dev_reg, entry.entry_id)):
        if er.async_entries_for_device(ent_reg, device.id):
            continue
        # Nothing left on it: either the device of a vendor id the account no
        # longer lists, or a leftover from the days when the cloud entities joined
        # the Zigbee device. The lock's current device stays even when it is
        # momentarily empty, so a reload does not churn it.
        own_ids = {value for domain, value in device.identifiers if domain == DOMAIN}
        if own_ids & set(device_serials):
            continue
        if not dry_run:
            dev_reg.async_remove_device(device.id)
        report["removed_devices"].append(device.id)

    # Remember the best name we have for every lock, so a later re-registration
    # does not fall back to the vendor's default.
    names = lock_names(entry)
    changed = False
    for device_id, serial in device_serials.items():
        if names.get(serial):
            continue
        preferred = _preferred_device(dev_reg, serial, entry.entry_id)
        name = _best_name(preferred)
        if name:
            names[serial] = name
            report["names"][serial] = name
            changed = True
    if changed and not dry_run:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, OPTION_LOCK_NAMES: names}
        )

    if any(report[key] for key in ("migrated", "removed_entities", "removed_devices")):
        _LOGGER.info(
            "Cloud registry cleanup: %s migrated, %s entities and %s devices removed%s",
            len(report["migrated"]),
            len(report["removed_entities"]),
            len(report["removed_devices"]),
            " (dry run)" if dry_run else "",
        )
    return report
