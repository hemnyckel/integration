"""The mirror engine — the emulator (app side) against the real lock.

Owns the app side (the emulator on the Nimly bridge) and mirrors it against the real
- app -> lock:  lock/unlock, PIN, fingerprint
- lock -> app:  lock/unlock, volume, auto-lock, battery, notifications (action/source/slot)

Everything happens in code: the user writes no automations and no YAML.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
import time
from collections import deque
from datetime import timedelta
from typing import Any
from collections.abc import Callable

import aiohttp

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from ..const import (
    ACTION_LOCK,
    ACTION_NAMES,
    ACTION_UNLOCK,
    CH_ACTIVITY,
    CH_AUTOLOCK,
    CH_BATTERY,
    CH_CLOUD,
    CH_FINGERPRINT,
    CH_LOCK,
    CH_PIN,
    CH_SYNC,
    CH_VOLUME,
    CMD_AUTOLOCK,
    CMD_BATTERY,
    CMD_EVENT,
    CMD_GET_STATE,
    CMD_FACTORY_RESET,
    CMD_LOCK,
    CMD_OTA,
    CMD_UNLOCK,
    CMD_VOLUME,
    CONF_CHANNELS,
    CONF_DEVICE_IDENTITY,
    CONF_ENABLED,
    CONF_OTA_MANIFEST_URL,
    CONF_TYPE,
    DEFAULT_ENDPOINT,
    DEFAULT_OTA_MANIFEST_URL,
    DOMAIN,
    ECHO_WINDOW,
    EVENT_JOURNAL,
    EVENT_NIMLY_CLOUD_ACTIVITY,
    EV_ACTION,
    EV_FP_CLEAR,
    EV_FP_ENROLL,
    EV_TAG_SCAN,
    EV_TAG_CLEAR,
    EV_PIN_CLEAR,
    EV_PIN_SET,
    EV_VOLUME,
    EV_AUTOLOCK,
    HEALTH_INTERVAL,
    HEALTH_TIMEOUT,
    HELLO_GAP,
    HUMAN_SOURCES,
    MANIFEST_REFRESH,
    SOURCE_NAMES,
    SRC_FINGERPRINT,
    SRC_KEYPAD,
    SRC_RFID,
    TOPIC_BATTERY,
    TOPIC_BRIDGE_INFO,
    TOPIC_BRIDGE_TO_HA,
    TOPIC_OTA,
    TOPIC_HA_TO_BRIDGE,
    TOPIC_PIN,
    TOPIC_STATE,
    TYPE_CLOUD,
    ZCL_CMD_FP_CLEAR,
    ZCL_CMD_FP_ENROLL,
    ZCL_CMD_TAG_SCAN,
    ZCL_CMD_TAG_CLEAR,
)

from .facts import (
    compute_settings_drift,
    placeholder_slot_name,
    suggest_user_name,
    vendor_volume,
)
from .guests import (
    GUEST_CREATED,
    GUEST_EXPIRED,
    GUEST_REVOKED,
    GUEST_USED,
    GUEST_WINDOW_CLOSE,
    GUEST_WINDOW_OPEN,
    code_owner,
    expired_slots,
    generate_code,
    is_expired,
    normalize_until,
    pick_slot,
    valid_code,
)
from .journal import (
    ORIGIN_CLOUD,
    ORIGIN_HA,
    ORIGIN_LOCK,
    add as journal_add,
    make_entry,
    query as journal_query,
    summarize as journal_summarize,
    trim as journal_trim,
)
from .pin_rules import check_credential_slot, first_user_slot, pin_capacity
from .schedule import describe as schedule_describe
from .schedule import in_window, next_boundary, normalize_windows
from .slot_virtual import (
    BLOCKED,
    CLEAR,
    IGNORE,
    MOVE,
    OPTION_SLOT_MAP,
    OPTION_SLOT_BINDS,
    PASS,
    dump_map,
    load_map,
    resolve_clear,
    resolve_write,
    virtual_of,
)
from .slots import SlotTable
from .zha_link import FACTS_ATTRIBUTES, ZhaLink

_LOGGER = logging.getLogger(__name__)

# Seconds between opportunistic facts reads right after the lock was awake.
FACTS_MIN_INTERVAL = 300


class MirrorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds the app side state and runs the mirroring in both directions."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        lock_entity_id: str,
        prefix: str,
        channels: dict[str, bool],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=MANIFEST_REFRESH),
        )
        self.entry = entry
        self.lock_entity_id = lock_entity_id
        self.prefix = prefix.rstrip("/")
        self.channels = channels
        self.master_enabled = True

        # App-side state (mirrored in the entities)
        self.app_locked: bool | None = None
        self.app_battery: int | None = None
        self.app_volume: int | None = None
        self.app_autolock: bool | None = None
        self.bridge_online = False
        self.emulator_joined: bool | None = None
        self._not_joined_since: float | None = None
        self._cloud_seen_at: float = 0.0
        self._cloud_expect_after: float | None = None
        self.firmware: str | None = None
        self.emulator_ieee: str | None = None
        self.bridge_info: dict[str, Any] = {}
        self.ota_status: dict[str, Any] | None = None
        self.firmware_manifest: dict[str, Any] | None = None
        self.ota_manifest_url = (
            entry.options.get(CONF_OTA_MANIFEST_URL)
            or entry.data.get(CONF_OTA_MANIFEST_URL)
            or DEFAULT_OTA_MANIFEST_URL
        )
        self.last_event: dict[str, Any] | None = None
        self.slots = SlotTable(hass, entry)
        # Virtual app slots: the vendor app's slot number -> the real slot that
        # holds the credential. Empty while the app's numbers pass through.
        self.slot_map: dict[int, int] = load_map(entry.options.get(OPTION_SLOT_MAP))
        # Binds are the app's copy of a code we already hold: the vendor slot is
        # tied to the guest's real slot, but the slot stays ours — only a
        # relocation or a passthrough makes the app the slot's owner.
        self.bound: dict[int, int] = load_map(entry.options.get(OPTION_SLOT_BINDS))
        self.lock_facts: dict[str, Any] = {}
        self.lock_facts_at: str | None = None
        self.settings_drift: dict[str, dict[str, Any]] = {}
        self._facts_refresh_monotonic: float = 0.0
        self._facts_task: asyncio.Task[Any] | None = None
        self.journal: list[dict[str, Any]] = []
        self._journal_path = hass.config.path(
            ".nimly", f"journal_{entry.entry_id}.jsonl"
        )
        self._journal_lock = asyncio.Lock()
        self.guests: dict[str, dict[str, Any]] = {}
        # The next finger enroll is a cloud-side replay (the vendor picks the
        # slot, so it is not known in advance); recorded for the catalog, never
        # mirrored to the lock, whose fingerprint template already exists.
        self._replay_enroll_until: float = 0.0
        self._guest_unsubs: dict[str, Callable[[], None]] = {}
        self.zha: ZhaLink | None = None
        self.last_error: str | None = None
        self.counters: dict[str, int] = {
            "app_to_lock": 0,
            "lock_to_app": 0,
            "events": 0,
            "errors": 0,
        }

        # Derived entities and metadata
        self.related: dict[str, str] = {}
        self.ieee: str | None = None
        self.endpoint_id: int = DEFAULT_ENDPOINT

        # Echo suppression: states we ordered ourselves (so they do not bounce back)
        self._commanded: deque[tuple[bool, float]] = deque(maxlen=8)
        self._last_state_rx: float = 0.0
        self._last_hello: float = 0.0
        self._unsubs: list[Callable[[], None]] = []
        self._started = False

    # -- public helpers -----------------------------------------------------

    def channel(self, key: str) -> bool:
        """The channel's configured mode, regardless of the master switch."""
        return bool(self.channels.get(key, False))

    def active(self, key: str) -> bool:
        """The channel is on and the master mirror is on."""
        return self.master_enabled and self.channel(key)

    @property
    def mirror_enabled(self) -> bool:
        return any(
            self.channel(key)
            for key in (CH_LOCK, CH_ACTIVITY, CH_PIN, CH_FINGERPRINT, CH_VOLUME, CH_AUTOLOCK, CH_BATTERY)
        )

    def _topic(self, suffix: str) -> str:
        return f"{self.prefix}/{suffix}"

    # -- lifecycle ----------------------------------------------------------

    async def async_setup(self) -> None:
        """Subscribe to MQTT and to the real lock. Idempotent."""
        if self._started:
            return
        self._started = True
        self._discover_lock_metadata()
        if self.ieee:
            self.zha = ZhaLink(
                self.hass,
                self.ieee,
                self.lock_entity_id,
                self.endpoint_id,
                self._on_lock_activity,
            )
            imported = self.slots.import_from_onesti(self.hass, self.ieee)
            if imported:
                _LOGGER.info("Imported %s slots from onesti_lock", imported)
            corrected = self.slots.correct_fingerprints()
            if corrected:
                _LOGGER.info(
                    "Corrected %s fingerprint mark(s) against the lock's table",
                    corrected,
                )

        self._unsubs.append(
            await mqtt.async_subscribe(
                self.hass, self._topic(TOPIC_BRIDGE_TO_HA), self._on_bridge_to_ha, qos=1
            )
        )
        self._unsubs.append(
            await mqtt.async_subscribe(
                self.hass, self._topic(TOPIC_STATE), self._on_state, qos=1
            )
        )
        self._unsubs.append(
            await mqtt.async_subscribe(
                self.hass, self._topic(TOPIC_BATTERY), self._on_battery, qos=1
            )
        )
        self._unsubs.append(
            await mqtt.async_subscribe(
                self.hass, self._topic(TOPIC_PIN), self._on_pin, qos=1
            )
        )
        self._unsubs.append(
            await mqtt.async_subscribe(
                self.hass, TOPIC_BRIDGE_INFO, self._on_bridge_info, qos=1
            )
        )
        self._unsubs.append(
            await mqtt.async_subscribe(
                self.hass, self._topic(TOPIC_OTA), self._on_ota, qos=1
            )
        )

        watched = [self.lock_entity_id]
        for key in ("volume", "autolock", "battery"):
            if ent := self.related.get(key):
                watched.append(ent)
        self._unsubs.append(
            async_track_state_change_event(self.hass, watched, self._on_tracked_change)
        )

        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_NIMLY_CLOUD_ACTIVITY, self._on_cloud_activity)
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED, self._on_device_registry_updated
            )
        )

        # HA -> app: the slot table's occupancy goes to the app, so a slot freed
        # in HA frees in the app too.
        if self.active(CH_PIN):
            self.hass.async_create_task(self._async_sync_slots())
        if self.zha is not None:
            self.zha.ensure_listener()
            # Prime the facts best-effort. No wake-up: a sleeping lock is simply
            # read the next time it is awake for some other reason.
            self.hass.async_create_task(self.async_refresh_lock_facts(wake=False))
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._async_health, timedelta(seconds=HEALTH_INTERVAL)
            )
        )

        await self._async_load_journal()
        self._load_guests()
        self._sync_device_identity()
        self._migrate_slot_binds()
        await self._async_resume_guests()
        await self._async_sync_to_app()
        self._publish_snapshot()
        await self._async_health()

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self.zha is not None:
            self.zha.detach()
        self._started = False

    # -- device naming ------------------------------------------------------

    @staticmethod
    def _normalise_serial(value: Any) -> str:
        return str(value or "").replace(":", "").replace("-", "").replace(".", "").lower()

    def _device_serial_match(self, device: Any) -> bool:
        """Whether a registry device carries this lock's module serial."""
        mine = self._normalise_serial(self.ieee)
        if not mine:
            return False
        values = [value for _kind, value in device.connections]
        values += [value for _kind, value in device.identifiers]
        return any(self._normalise_serial(value) == mine for value in values)

    def _serial_devices(self) -> list[Any]:
        """Registry devices that carry this lock's module serial."""
        return [
            device
            for device in dr.async_get(self.hass).devices
            if self._device_serial_match(device)
        ]

    @callback
    def _apply_device_identity(self) -> None:
        """Name a freshly joined module from what the user called it before.

        Nothing is invented: only a name (and area) the user themselves gave
        this serial in an earlier life of the device is re-applied, so a
        factory reset or a re-pair comes back named correctly.
        """
        stored = dict(self.entry.options.get(CONF_DEVICE_IDENTITY) or {})
        registry = dr.async_get(self.hass)
        for device in self._serial_devices():
            fix: dict[str, Any] = {}
            if not device.name_by_user and stored.get("name"):
                fix["name_by_user"] = stored["name"]
            if not device.area_id and stored.get("area_id"):
                fix["area_id"] = stored["area_id"]
            if fix:
                _LOGGER.info(
                    "Named %s from the remembered identity: %s",
                    self._normalise_serial(self.ieee),
                    fix,
                )
                registry.async_update_device(device.id, **fix)

    @callback
    def _sync_device_identity(self) -> None:
        """Startup pass: remember the module's current name, then re-apply it."""
        for device in self._serial_devices():
            self._remember_device_identity(device)
        self._apply_device_identity()

    @callback
    def _remember_device_identity(self, device: Any) -> None:
        """Store the user's name for this serial so a rejoin can re-apply it."""
        stored = dict(self.entry.options.get(CONF_DEVICE_IDENTITY) or {})
        changed = False
        remembered = device.name_by_user
        if not remembered and device.name:
            derived = " ".join(
                part for part in (device.manufacturer, device.model) if part
            )
            if device.name != derived:
                remembered = device.name
        if remembered and remembered != stored.get("name"):
            stored["name"] = remembered
            changed = True
        if device.area_id and device.area_id != stored.get("area_id"):
            stored["area_id"] = device.area_id
            changed = True
        if changed:
            _LOGGER.info(
                "Remembered device identity for %s: name=%s area=%s",
                self._normalise_serial(self.ieee),
                stored.get("name"),
                stored.get("area_id"),
            )
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={**self.entry.options, CONF_DEVICE_IDENTITY: stored},
            )

    @callback
    def _on_device_registry_updated(self, event: Any) -> None:
        """Name a device that just joined; remember a rename for the next join."""
        data = event.data if isinstance(event.data, dict) else {}
        if data.get("action") not in ("create", "update"):
            return
        device = dr.async_get(self.hass).async_get(data.get("device_id"))
        if device is None:
            return
        if not self._device_serial_match(device):
            return
        self._remember_device_identity(device)
        self._apply_device_identity()

    # -- metadata -----------------------------------------------------------

    def _discover_lock_metadata(self) -> None:
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.lock_entity_id)
        if entry is None:
            _LOGGER.warning("The lock entity %s is not in the registry", self.lock_entity_id)
            return

        if entry.device_id:
            dev_reg = dr.async_get(self.hass)
            device = dev_reg.async_get(entry.device_id)
            if device:
                for domain, ident in device.identifiers:
                    if domain == "zha":
                        self.ieee = str(ident).lower()

            for other in registry.entities.values():
                if other.device_id != entry.device_id or other.disabled_by:
                    continue
                eid = other.entity_id
                if other.domain == "number" and "volume" in eid:
                    self.related["volume"] = eid
                elif other.domain == "switch" and (
                    "autorelock" in eid or "auto_lock" in eid
                ):
                    self.related["autolock"] = eid
                elif other.domain == "sensor" and "battery" in eid:
                    self.related["battery"] = eid
                elif other.domain == "sensor" and "last_action" in eid:
                    # The ZHA quirk encodes the endpoint in the unique_id (…-<ep>-last_action)
                    uid = (other.unique_id or "").rsplit("-", 2)
                    if len(uid) == 3 and uid[1].isdigit():
                        self.endpoint_id = int(uid[1])

        _LOGGER.debug(
            "mirror metadata: ieee=%s ep=%s relaterade=%s",
            self.ieee,
            self.endpoint_id,
            self.related,
        )

    # -- publishing ---------------------------------------------------------

    async def _async_publish(self, payload: dict[str, Any]) -> None:
        if payload.get("cmd") in (CMD_EVENT, CMD_LOCK, CMD_UNLOCK):
            # This should show up in the vendor cloud's feed shortly; the health
            # tick raises a repair when the feedback never arrives.
            self._cloud_expect_after = time.monotonic()
        try:
            await mqtt.async_publish(
                self.hass,
                self._topic(TOPIC_HA_TO_BRIDGE),
                json.dumps(payload, separators=(",", ":")),
                qos=1,
                retain=False,
            )
        except Exception as err:  # noqa: BLE001 - never crash an entity
            self.counters["errors"] += 1
            self.last_error = str(err)
            _LOGGER.error("Could not publish %s: %s", payload, err)

    @callback
    def _snapshot(self) -> dict[str, Any]:
        return {
            "app_locked": self.app_locked,
            "bridge_online": self.bridge_online,
            "emulator_joined": self.emulator_joined,
            "battery": self.app_battery,
            "volume": self.app_volume,
            "autolock": self.app_autolock,
            "last_event": self.last_event,
            "firmware": self.firmware,
            "emulator_ieee": self.emulator_ieee,
            "bridge_info": dict(self.bridge_info),
            "ota_status": dict(self.ota_status or {}),
            "firmware_manifest": self.firmware_manifest,
            "ota_manifest_url": self.ota_manifest_url,
            "counters": dict(self.counters),
            "last_error": self.last_error,
            "channels": dict(self.channels),
            "master_enabled": self.master_enabled,
            "slot_map": dump_map(self.slot_map),
            "bound_slots": dump_map(self.bound),
        }

    @callback
    def _publish_snapshot(self) -> None:
        self.async_set_updated_data(self._snapshot())

    async def _async_sync_slots(self) -> None:
        # The app may only ever see its own slot numbers: a relocated credential
        # is published under its virtual number, never under the real one.
        published: set[int] = set()
        for slot, _data in self.slots.items():
            shown = self._virtual_slot(slot)
            if shown is None:
                shown = slot
            if shown in published:
                continue
            published.add(shown)
            await self._async_publish_slot(shown, self.slots.occupied(slot))
        for virtual, real in self.slot_map.items():
            if virtual not in published:
                published.add(virtual)
                await self._async_publish_slot(virtual, self.slots.occupied(real))

    async def _async_publish_slot(self, slot: int, occupied: bool) -> None:
        await self._async_publish({"cmd": "pin_status", "slot": slot, "set": occupied})

    # -- app slot virtualization --------------------------------------------

    def _local_pin_slots(self) -> set[int]:
        """Slots the local side owns and the app must never overwrite.

        That is every slot a guest calls home — also outside its window, when
        the code is cleared but the slot is reserved for the next opening — and
        every slot that currently holds a local PIN. App-owned slots are
        excluded: a passthrough write is remembered as an identity mapping, so
        the app always owns what it wrote, whichever real slot it ended up in.
        """
        app_owned = set(self.slot_map.values())
        owned = {
            slot
            for slot, data in self.slots.items()
            if data.get("has_pin") and slot not in app_owned
        }
        owned |= {
            int(key)
            for key in self.guests
            if key.isdigit() and int(key) not in app_owned
        }
        return owned

    def _slot_bounds(self) -> tuple[int, int]:
        return first_user_slot(self.entry.options), pin_capacity(self.lock_facts)

    def _virtual_slot(self, real: int) -> int | None:
        """The app's slot number for a real slot, when it owns it."""
        return virtual_of(real, self.slot_map)

    def _save_slot_map(self) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={**self.entry.options, OPTION_SLOT_MAP: dump_map(self.slot_map)},
        )

    def _save_bound_map(self) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={**self.entry.options, OPTION_SLOT_BINDS: dump_map(self.bound)},
        )

    def _migrate_slot_binds(self) -> None:
        """Move binds that older versions recorded as app ownership.

        A slot_map entry whose real slot holds a local PIN is a bind (the app's
        copy of our own code), not ownership: left in place it would block every
        local clear of that slot and hide the slot from the local side.
        """
        local = {slot for slot, data in self.slots.items() if data.get("has_pin")}
        local |= {int(key) for key in self.guests if key.isdigit()}
        moved: dict[int, int] = {}
        for virtual, real in list(self.slot_map.items()):
            if real in local:
                self.slot_map.pop(virtual, None)
                moved[virtual] = real
        if moved:
            for virtual, real in moved.items():
                self.bound.setdefault(virtual, real)
            self._save_slot_map()
            self._save_bound_map()

    def _issue_id(self, key: str) -> str:
        """A repair issue id that is unique to this entry.

        Several mirrors (one per lock) share the issue registry, so a bare
        "new_slot_5" would point at whichever lock happened to raise it first.
        """
        return f"{key}_{self.entry.entry_id[:8]}"

    async def _async_slot_conflict(self, virtual: int, reason: str) -> None:
        """Journal and surface an app provisioning we could not place."""
        await self._async_journal_add(
            make_entry(
                action="slot_conflict",
                time=dt_util.utcnow().isoformat(),
                origin=ORIGIN_HA,
                slot=virtual,
                detail=reason,
            )
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(f"slot_conflict_{virtual}"),
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="slot_conflict",
            translation_placeholders={"slot": str(virtual), "reason": reason},
        )

    def _slot_is_named(self, slot: int) -> bool:
        """A real name, not the import's placeholder (which may be replaced)."""
        return not placeholder_slot_name(self.slots.get(slot).get("name"))

    @callback
    def _flag_new_slot(self, slot: int, kind: str) -> None:
        """Raise a fixable repair when the app creates a slot we have no name for."""
        if slot < 3 or not self.active(CH_PIN) or self._slot_is_named(slot):
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(f"new_slot_{slot}"),
            is_fixable=True,
            is_persistent=True,
            data={"slot": slot, "entry_id": self.entry.entry_id},
            severity=ir.IssueSeverity.WARNING,
            translation_key="new_slot",
            translation_placeholders={"slot": str(slot), "kind": kind},
        )

    async def async_set_slot_name(self, slot: int, name: str) -> None:
        """Write a slot name into the table and clear the repair."""
        self.slots.set_name(slot, name)
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(f"new_slot_{slot}"))
        await self._async_publish_slot(slot, self.slots.occupied(slot))
        self._publish_snapshot()
        _LOGGER.info("Named slot %s as %s", slot, name)
        await self._async_journal_add(
            make_entry(
                action="slot_named",
                time=dt_util.utcnow().isoformat(),
                origin=ORIGIN_HA,
                slot=slot,
                detail=name,
            )
        )

    def suggest_slot_name(self, slot: int) -> str | None:
        """A cloud user name for a freshly learned slot, when exactly one fits.

        The cloud maps users to credentials but never exposes slot numbers (the
        gateway translates internally), so the suggestion is conservative and
        the user still confirms it in the repair flow.
        """
        wanted = set(self.slots.credentials(slot))
        if not wanted or self._slot_is_named(slot):
            return None
        used = {
            str(data.get("name") or "").lower()
            for _slot, data in self.slots.items()
            if data.get("name")
        }
        # The journal remembers names the cloud attached to this slot's events
        # (a merged local+cloud pair), which covers guests that the location's
        # member list does not contain.
        for entry in reversed(self.journal):
            if entry.get("slot") != slot or not entry.get("name"):
                continue
            name = str(entry["name"])
            if name.lower() not in used:
                return name
            break
        for coordinator in self.hass.data.get(DOMAIN, {}).values():
            access = getattr(coordinator, "access", None)
            users = getattr(coordinator, "users", None)
            devices = getattr(coordinator, "devices", None)
            if not access or not users or not devices:
                continue
            device_id = self._cloud_device_id(devices)
            if device_id is None:
                continue
            return suggest_user_name(wanted, used, users, access.get(device_id) or [])
        return None

    def _cloud_device_id(self, devices: list[dict[str, Any]]) -> str | None:
        """The cloud device whose serial number is this lock's module (IEEE)."""
        mine = str(self.ieee or "").replace(":", "").replace("-", "").lower()
        if not mine:
            return None
        for device in devices:
            serial = (
                str(device.get("serialNumber") or "")
                .replace(":", "")
                .replace("-", "")
                .lower()
            )
            if serial and serial == mine:
                return str(device.get("id") or "") or None
        return None

    async def async_set_slot_pin(self, slot: int, code: str) -> None:
        """Write a PIN to a slot on the real lock (config UI and service path)."""
        await self._async_set_pin(slot, code)

    async def async_clear_slot(self, slot: int) -> None:
        """Clear a slot's credential on the real lock and forget it locally.

        A slot the app owns is cleared the way the app's own clear would be —
        the local side takes the slot back — because this is the tool for the
        stale leftovers (a guest deleted in the app whose code never left the
        lock); refusing to touch them would leave codes nobody can account for.
        """
        owner = self._virtual_slot(slot)
        await self._async_clear_pin(slot, virtual_slot=slot)
        if owner is not None:
            self.slot_map.pop(owner, None)
            self._save_slot_map()
        self.slots.clear(slot)
        # The repairs that asked for a name or reported a conflict point at a
        # slot that no longer holds anything.
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(f"new_slot_{slot}"))
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(f"slot_conflict_{slot}"))
        await self._async_publish_slot(slot, False)
        self._publish_snapshot()

    async def async_wipe_credentials(
        self, *, dry_run: bool, confirm: str | None
    ) -> dict[str, Any]:
        """Empty the lock's user space and every guest, masters untouched.

        Slots below the master floor are never written (the pin rules refuse
        them anyway). The plan lists exactly what a real run does, and a real
        run also needs ``confirm="WIPE"``: the one command that removes
        everything takes two deliberate steps to fire.
        """
        if not dry_run and confirm != "WIPE":
            raise ValueError("pass confirm: WIPE to run the wipe for real")

        floor = first_user_slot(self.entry.options)
        slot_numbers = {slot for slot, _data in self.slots.items() if slot >= floor} | {
            int(real) for real in self.slot_map.values() if int(real) >= floor
        }
        report: dict[str, Any] = {
            "dry_run": dry_run,
            "master_floor": floor,
            "slots": [],
            "guests": [],
            "tags": [],
            "cloud": {},
            "errors": [],
        }

        # The lock cannot report which slots hold something (the module ignores
        # read commands and exposes no occupancy attribute), so the catalog is
        # only a map, not proof. The sweep therefore covers the whole user
        # range above the master floor — an unknown credential in an
        # unlisted slot is cleared too.
        capacity = max(pin_capacity(self.lock_facts), floor + 1)
        report["sweep"] = {"from": floor, "to": capacity - 1, "count": capacity - floor}
        report["slots"] = [
            {"slot": slot, "credentials": self.slots.credentials(slot)}
            for slot in sorted(slot_numbers)
        ]
        if not dry_run:
            for slot in range(floor, capacity):
                try:
                    await self.async_clear_slot(slot)
                except Exception as err:  # noqa: BLE001
                    report["errors"].append(f"slot {slot} pin: {err}")
                try:
                    await self._async_zcl(ZCL_CMD_FP_CLEAR, slot)
                except Exception as err:  # noqa: BLE001
                    report["errors"].append(f"slot {slot} finger: {err}")

        # A tag is keyed by the vendor id its enroll flow journaled; sweep the
        # ones we can prove were ever enrolled.
        tag_ids = sorted(
            {
                int(match, 16)
                for entry in self.journal
                if entry.get("action") == "tag_scan"
                for match in re.findall(
                    r"0x([0-9a-fA-F]{4})", str(entry.get("detail") or "")
                )
            }
        )
        report["tags"] = [f"0x{tag:04x}" for tag in tag_ids]
        if not dry_run:
            for tag in tag_ids:
                try:
                    await self._async_zcl(ZCL_CMD_TAG_CLEAR, tag)
                except Exception as err:  # noqa: BLE001
                    report["errors"].append(f"tag 0x{tag:04x}: {err}")

        for key, guest in sorted(self.guests.items()):
            report["guests"].append({"slot": int(key), "name": guest.get("name")})
            if dry_run:
                continue
            try:
                await self.async_revoke_guest(int(key))
            except Exception as err:  # noqa: BLE001
                report["errors"].append(f"guest {key}: {err}")

        from ..cloud.coordinator import NimlyCloudCoordinator
        from ..cloud.sync import async_wipe_guest_users

        try:
            for item in self.hass.data.get(DOMAIN, {}).values():
                if isinstance(item, NimlyCloudCoordinator):
                    report["cloud"] = await async_wipe_guest_users(
                        item, dry_run=dry_run
                    )
        except Exception as err:  # noqa: BLE001 - report it, keep the summary
            report["errors"].append(f"cloud: {err}")

        if not dry_run:
            await self._async_journal_add(
                make_entry(
                    action="wipe",
                    time=dt_util.utcnow().isoformat(),
                    origin=ORIGIN_HA,
                    detail=(
                        f"swept {report['sweep']['count']} slots, "
                        f"{len(report['guests'])} guests, "
                        f"{len(report['tags'])} tags"
                    ),
                )
            )
        self._publish_snapshot()
        return report

    async def async_clear_repairs(self) -> int:
        """Delete every repair issue this integration raised.

        Home Assistant offers no delete in the UI — only dismiss, which leaves
        the issue in the registry — so the integration that raised it is the
        one that can really remove it. The registry is iterated, so ids from
        older versions (without the entry suffix) go too.
        """
        registry = ir.async_get(self.hass)
        removed = 0
        for domain, issue_id in list(getattr(registry, "issues", {}) or {}):
            if domain != DOMAIN:
                continue
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            removed += 1
        return removed

    async def async_read_lock_attributes(
        self, attributes: list[int | str] | None = None
    ) -> dict[str, Any]:
        """Read standard DoorLock attributes for diagnostics (never credentials)."""
        if self.zha is None:
            return {"success": {}, "failure": {"error": "the ZHA link is not ready"}}
        success, failure = await self.zha.read_attributes(attributes)
        return {"success": success, "failure": failure}

    async def async_refresh_lock_facts(self, *, wake: bool = True) -> dict[str, Any]:
        """Read the lock's own capabilities and settings (never credentials).

        Called on demand by the services, and opportunistically right after the
        lock was awake for some other reason. ``wake=False`` never actuates it.
        """
        if self.zha is None:
            return self.lock_facts
        success, _failure = await self.zha.read_attributes(FACTS_ATTRIBUTES, wake=wake)
        if not success:
            return self.lock_facts
        self.lock_facts.update(success)
        self.lock_facts_at = dt_util.utcnow().isoformat()
        self.settings_drift = compute_settings_drift(
            self.lock_facts, self.app_autolock, self.app_volume
        )
        if self.settings_drift:
            _LOGGER.warning(
                "Lock settings differ from the app's record: %s", self.settings_drift
            )
        # Fresh facts are exactly when a drifted app record can be corrected
        # (and when a fresh registration's defaults show up).
        await self._async_heal_app_settings()
        self._publish_snapshot()
        return self.lock_facts

    async def _async_heal_app_settings(self) -> None:
        """Correct the app's record when it differs from the lock's own settings.

        A fresh cloud registration starts from the app's defaults, so after a
        (re)pairing the record can disagree with the lock; the lock is the source
        of truth. The comparison reads the cloud's own view (its settings object
        and the module values it reports), not the local mirror's copy.
        """
        for coordinator in self.hass.data.get(DOMAIN, {}).values():
            devices = getattr(coordinator, "devices", None)
            setting = getattr(coordinator, "device_setting", None)
            state = getattr(coordinator, "device_state", None)
            if not devices:
                continue
            device_id = self._cloud_device_id(devices)
            if device_id is None:
                continue
            payload: dict[str, Any] = {}
            lock_auto = self.lock_facts.get("auto_relock_time")
            if lock_auto is not None and setting is not None:
                if bool(setting(device_id, "autolock")) != bool(lock_auto):
                    payload["autolock"] = bool(lock_auto)
            lock_volume = self.lock_facts.get("sound_volume")
            if lock_volume is not None and state is not None:
                module_volume = state(device_id, "lock", "soundvolume")
                if isinstance(module_volume, int) and module_volume != int(lock_volume):
                    vendor = vendor_volume(int(lock_volume))
                    if vendor:
                        payload["volume"] = vendor
            if payload:
                _LOGGER.info("Healing the app's settings from the lock: %s", payload)
                await self._async_push_app_setting(payload)
            return

    def _schedule_facts_refresh(self) -> None:
        """Background facts refresh - the lock is awake right now anyway."""
        now = time.monotonic()
        if now - self._facts_refresh_monotonic < FACTS_MIN_INTERVAL:
            return
        if self._facts_task is not None and not self._facts_task.done():
            return
        self._facts_refresh_monotonic = now
        self._facts_task = self.hass.async_create_task(
            self.async_refresh_lock_facts(wake=False)
        )

    async def async_set_lock_autolock(self, enabled: bool) -> dict[str, Any]:
        """Write the lock's own auto-relock setting and read it back."""
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        await self.zha.write_attributes({"auto_relock_time": 1 if enabled else 0})
        facts = await self.async_refresh_lock_facts(wake=True)
        actual = facts.get("auto_relock_time")
        if actual is None:
            raise RuntimeError("the lock did not report the setting back")
        if bool(actual) != bool(enabled):
            raise RuntimeError("the lock kept a different auto-lock setting")
        await self._async_push_app_setting({"autolock": bool(enabled)})
        await self._async_journal_add(
            make_entry(
                action="setting_changed",
                time=dt_util.utcnow().isoformat(),
                origin=ORIGIN_HA,
                detail=f"auto_lock={'on' if enabled else 'off'}",
            )
        )
        return facts

    async def async_set_lock_volume(self, level: int) -> dict[str, Any]:
        """Write the lock's own sound volume and read it back."""
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        await self.zha.write_attributes({"sound_volume": int(level)})
        facts = await self.async_refresh_lock_facts(wake=True)
        actual = facts.get("sound_volume")
        if actual is None:
            raise RuntimeError("the lock did not report the volume back")
        if int(actual) != int(level):
            raise RuntimeError("the lock kept a different volume")
        vendor = vendor_volume(int(level))
        if vendor is not None:
            await self._async_push_app_setting({"volume": vendor})
        await self._async_journal_add(
            make_entry(
                action="setting_changed",
                time=dt_util.utcnow().isoformat(),
                origin=ORIGIN_HA,
                detail=f"sound_volume={int(level)}",
            )
        )
        return facts

    async def _async_push_app_setting(self, payload: dict[str, Any]) -> None:
        """Best-effort: keep the app's record in step with the lock's own setting.

        The local path never depends on this; a failure is logged and dropped,
        and the drift sensor keeps showing the difference until it is gone.
        """
        for coordinator in self.hass.data.get(DOMAIN, {}).values():
            push = getattr(coordinator, "async_push_settings", None)
            devices = getattr(coordinator, "devices", None)
            if push is None or not devices:
                continue
            device_id = self._cloud_device_id(devices)
            if device_id is None:
                continue
            try:
                await push(device_id, payload)
            except Exception as err:  # noqa: BLE001 - convenience only
                _LOGGER.debug("Could not push settings to the cloud: %s", err)
            return

    async def async_reset_app_registration(self) -> dict[str, Any]:
        """Remove this lock's device record from the vendor account.

        Exactly what the app's "remove device" does, so a recovery does not
        depend on finding that menu: the cloud refuses to (re)pair a module
        whose serial is still registered. Run the app's add-device search
        afterwards; the emulator steers on its own.
        """
        for coordinator in self.hass.data.get(DOMAIN, {}).values():
            devices = getattr(coordinator, "devices", None)
            api = getattr(coordinator, "api", None)
            if not devices or api is None:
                continue
            device_id = self._cloud_device_id(devices)
            if device_id is None:
                continue
            # SAFETY: only ever delete the record whose serial is this lock's.
            meta = (getattr(coordinator, "device_meta", {}) or {}).get(device_id) or {}
            serial = str(meta.get("serialNumber") or "").replace(":", "").lower()
            mine = str(self.ieee or "").replace(":", "").lower()
            if not mine or serial != mine:
                return {"reset": False, "reason": "serial mismatch, refusing"}
            await api.async_delete_device(device_id)
            await coordinator.async_request_refresh()
            await self._async_journal_add(
                make_entry(
                    action="app_registration_reset",
                    time=dt_util.utcnow().isoformat(),
                    origin=ORIGIN_HA,
                    detail="the app's device record was removed; run add-device",
                )
            )
            _LOGGER.warning("Removed the app's device record for %s", self.ieee)
            return {"device_id": device_id, "reset": True}
        return {"reset": False, "reason": "no cloud device matches this lock"}

    # -- journal -------------------------------------------------------------

    def journal_entries(self, **filters: Any) -> list[dict[str, Any]]:
        """Query the timeline (the fetch_journal service path)."""
        return journal_query(self.journal, **filters)

    def journal_summary(self) -> dict[str, int]:
        return journal_summarize(self.journal, now=time.time())

    async def _async_load_journal(self) -> None:
        """Read the on-disk journal once at startup; a broken file starts empty."""

        def _read() -> list[dict[str, Any]]:
            path = pathlib.Path(self._journal_path)
            if not path.exists():
                return []
            loaded: list[dict[str, Any]] = []
            try:
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(entry, dict) and entry.get("time"):
                            loaded.append(entry)
            except OSError as err:
                _LOGGER.warning("Could not read the journal: %s", err)
                return []
            return loaded

        self.journal = await self.hass.async_add_executor_job(_read)
        if journal_trim(self.journal, now=time.time()):
            await self._async_save_journal(rewrite=True)

    async def _async_journal_add(self, candidate: dict[str, Any]) -> None:
        """Store one event, merging it with its twin from the other source."""
        stored, merged, trimmed = journal_add(self.journal, candidate, now=time.time())
        await self._async_save_journal(rewrite=merged or trimmed, entry=stored)
        if not merged:
            self.hass.bus.async_fire(EVENT_JOURNAL, dict(stored))
        self._publish_snapshot()

    async def _async_save_journal(
        self, *, rewrite: bool, entry: dict[str, Any] | None = None
    ) -> None:
        """Rewrite the file for merges and trims, else append the stored entry.

        The entry to append is passed in rather than read back from the list:
        an append queued behind another add must not write whatever happens to
        be newest when the executor runs. Serialized: two writers sharing the
        temporary file used to race, and the loser's rename failed with ENOENT,
        silently dropping an entry.
        """
        to_append = entry if entry is not None else (self.journal[-1] if self.journal else None)

        def _write() -> None:
            path = pathlib.Path(self._journal_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if rewrite or not path.exists():
                tmp = path.with_suffix(".tmp")
                with tmp.open("w", encoding="utf-8") as handle:
                    for entry in self.journal:
                        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                tmp.replace(path)
            elif to_append is not None:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(to_append, ensure_ascii=False) + "\n")

        async with self._journal_lock:
            try:
                await self.hass.async_add_executor_job(_write)
            except OSError as err:
                _LOGGER.warning("Could not write the journal: %s", err)

    # -- guest codes ---------------------------------------------------------

    def list_guests(self) -> list[dict[str, Any]]:
        """The active guests with their live state (never the codes themselves)."""
        rows = self.guest_rows()
        order = sorted(rows, key=lambda key: int(key) if key.isdigit() else 0)
        return [rows[key] for key in order]

    def _load_guests(self) -> None:
        stored = self.entry.options.get("guests")
        if isinstance(stored, dict):
            self.guests = {
                str(key): dict(value)
                for key, value in stored.items()
                if isinstance(value, dict)
            }

    async def _async_save_guests(self) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                "guests": {key: dict(value) for key, value in self.guests.items()},
            },
        )

    def cloud_user(self, slot: int) -> str | None:
        """The vendor uuid a local guest was synced to, if it has one."""
        stored = self.entry.options.get("cloud_users")
        if isinstance(stored, dict):
            value = stored.get(str(slot))
            if isinstance(value, str) and value:
                return value
        return None

    async def async_set_cloud_user(self, slot: int, user_id: str) -> None:
        """Remember which vendor identity a local guest maps to."""
        stored = dict(self.entry.options.get("cloud_users") or {})
        stored[str(slot)] = str(user_id)
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, "cloud_users": stored}
        )

    async def async_forget_cloud_identity(self, user_id: str) -> list[dict[str, Any]]:
        """Drop every catalog link to a vendor identity that no longer exists.

        Returns the links that were removed so the caller can report them; the
        local guests themselves stay, they simply lose their cloud mapping.
        """
        wanted = str(user_id)
        removed: list[dict[str, Any]] = []
        stored = dict(self.entry.options.get("cloud_users") or {})
        changed = False
        for slot, value in list(stored.items()):
            if value == wanted:
                stored.pop(slot, None)
                removed.append(
                    {"slot": int(slot) if str(slot).isdigit() else slot, "type": "pin"}
                )
                changed = True
        links = dict(self.cloud_links())
        for key, value in list(links.items()):
            if value == wanted:
                links.pop(key, None)
                slot, _, access_type = key.partition(":")
                removed.append(
                    {
                        "slot": int(slot) if slot.isdigit() else slot,
                        "type": access_type,
                    }
                )
                changed = True
        if changed:
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={
                    **self.entry.options,
                    "cloud_users": stored,
                    "cloud_links": links,
                },
            )
        return removed

    async def async_apply_guest_code(self, slot: int, code: str) -> None:
        """Write a new value for a guest's code into the lock and the catalog.

        The local side owns the value, so it goes into the lock first: the
        vendor's own write of the same code then binds to this slot instead of
        adding a second copy, and a later restore replays the new value.
        """
        guest = self.guests.get(str(slot))
        if not isinstance(guest, dict):
            raise RuntimeError(f"no guest in slot {slot}")
        await self._async_set_pin(
            slot,
            code,
            journal={"action": "code_changed", "name": guest.get("name")},
        )
        guest["code"] = str(code)
        await self._async_save_guests()
        self._publish_snapshot()

    def slot_for_cloud_user(self, user_id: str) -> int | None:
        """The slot of the local guest a vendor identity belongs to, if any."""
        for slot_key, guest in self.guests.items():
            if isinstance(guest, dict) and slot_key.isdigit():
                if self.cloud_user(int(slot_key)) == str(user_id):
                    return int(slot_key)
        return None

    def cloud_sync_candidates(self) -> list[dict[str, Any]]:
        """Local guests the cloud can be told about: a stored code, any kind.

        Recurring and permanent guests keep their code, so both are replayed
        after a loss and both may be told to the cloud; a temporary guest's
        value dies with the response that carried it.
        """
        rows: list[dict[str, Any]] = []
        for key, guest in self.guests.items():
            if not isinstance(guest, dict) or guest.get("kind") not in (
                "recurring",
                "permanent",
            ):
                continue
            name = str(guest.get("name") or "").strip()
            code = str(guest.get("code") or "")
            if not name or not code:
                continue
            try:
                slot = int(key)
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "slot": slot,
                    "name": name,
                    "code": code,
                    "user_id": self.cloud_user(slot),
                }
            )
        return rows

    async def async_journal_note(self, action: str, *, detail: str = "") -> None:
        """A public journal write for the other layers (the cloud sync)."""
        await self._async_journal_add(
            make_entry(
                action=action,
                time=dt_util.utcnow().isoformat(),
                origin=ORIGIN_HA,
                detail=detail,
            )
        )

    async def _async_cloud_sync_guest(self, slot: int, code: str | None) -> None:
        """Tell the vendor cloud about a guest we just created, when enabled.

        A code that exists only in the service response (a temporary guest) is
        passed in; a recurring or permanent guest keeps it in the entry options.
        Failure here must never fail the local creation, so everything is
        caught and logged.
        """
        if not self.active(CH_CLOUD):
            return
        from ..cloud.coordinator import NimlyCloudCoordinator
        from ..cloud.sync import async_sync_guest

        clouds = [
            item
            for item in self.hass.data.get(DOMAIN, {}).values()
            if isinstance(item, NimlyCloudCoordinator)
        ]
        if not clouds:
            return
        guest = dict(self.guests.get(str(slot)) or {})
        candidate = {
            "slot": slot,
            "name": str(guest.get("name") or "").strip(),
            "code": str(code or guest.get("code") or ""),
            "user_id": self.cloud_user(slot),
        }
        if not candidate["name"] or not candidate["code"]:
            return
        try:
            actions = await async_sync_guest(clouds[0], self, candidate, dry_run=False)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Cloud sync of slot %s failed", slot)
            return
        if any(
            action["action"] in ("create_guest", "adopt_guest", "create_access")
            for action in actions
        ):
            await self.async_journal_note(
                "cloud_synced",
                detail=f"slot {slot}",
            )

    async def _async_cloud_push_guest_update(
        self, slot: int, changes: dict[str, Any]
    ) -> bool:
        """Tell the vendor cloud about a rename, expiry or code we just made.

        Best effort by design: a slow or angry vendor must never fail the local
        edit. The journal records what happened and a repair says so when the
        app would otherwise keep showing the old value. Only guests the cloud
        already knows are pushed — creation goes through
        ``_async_cloud_sync_guest``.
        """
        if not self.active(CH_CLOUD):
            return True
        from ..cloud.coordinator import NimlyCloudCoordinator
        from ..cloud.sync import async_push_guest_update

        clouds = [
            item
            for item in self.hass.data.get(DOMAIN, {}).values()
            if isinstance(item, NimlyCloudCoordinator)
        ]
        if not clouds:
            return True
        issue_id = self._issue_id(f"cloud_push_{slot}")
        try:
            actions = await async_push_guest_update(clouds[0], self, slot, changes)
        except Exception:  # noqa: BLE001 - never fail the local edit
            _LOGGER.exception("Cloud push of slot %s failed", slot)
            await self.async_journal_note("cloud_update_failed", detail=f"slot {slot}")
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                is_persistent=False,
                data={"slot": slot, "entry_id": self.entry.entry_id},
                severity=ir.IssueSeverity.WARNING,
                translation_key="cloud_push_failed",
                translation_placeholders={
                    "slot": str(slot),
                    "name": str((self.guests.get(str(slot)) or {}).get("name") or ""),
                },
            )
            return False
        if not actions:
            return True
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        changed = sorted(
            {
                str(field)
                for action in actions
                for field in (action.get("changed") or [])
            }
            | {str(action.get("type")) for action in actions if action.get("type")}
        )
        await self.async_journal_note(
            "cloud_updated",
            detail=f"slot {slot}" + (f": {', '.join(changed)}" if changed else ""),
        )
        return True

    async def async_retry_cloud_push(self, slot: int) -> bool:
        """Re-send a guest's local state to the cloud (the repair's action).

        The values are the ones we already hold: name and validity are
        idempotent, and a stored code goes through the access replace. True
        means the cloud accepted everything.
        """
        guest = dict(self.guests.get(str(slot)) or {})
        if not guest:
            return False
        changes: dict[str, Any] = {}
        name = str(guest.get("name") or "").strip()
        if name:
            changes["name"] = name
        if "until" in guest:
            changes["until"] = str(guest.get("until") or "")
        code = str(guest.get("code") or "")
        if code:
            changes["code"] = code
        if not changes:
            return False
        return await self._async_cloud_push_guest_update(slot, changes)

    async def _async_cloud_remove_guest(self, slot: int, user_id: str) -> None:
        """After a revoke: take the guest's cloud accesses away, and its identity
        when no lock or other guest still needs it.

        Best effort — the audit reports whatever this could not do — and it
        runs as its own task so a slow vendor never delays the revoke.
        """
        if not self.active(CH_CLOUD):
            return
        from ..cloud.coordinator import NimlyCloudCoordinator
        from ..cloud.sync import cloud_device_for

        clouds = [
            item
            for item in self.hass.data.get(DOMAIN, {}).values()
            if isinstance(item, NimlyCloudCoordinator)
        ]
        if not clouds:
            return
        cloud = clouds[0]
        try:
            device_id = cloud_device_for(cloud, self)
            for device in cloud.home.get("devices") or []:
                device_key = str(device.get("id") or "")
                if not device_key or device_key != device_id:
                    continue
                for access in list(cloud.access.get(device_key, [])):
                    if str(access.get("userId")) != user_id:
                        continue
                    try:
                        await cloud.api.async_delete_access(
                            device_key, user_id, str(access.get("type"))
                        )
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.warning(
                            "Cloud access removal for %s on %s failed: %s",
                            user_id,
                            device_key,
                            err,
                        )
            keeps = False
            for device in cloud.home.get("devices") or []:
                device_key = str(device.get("id") or "")
                if not device_key or device_key == device_id:
                    continue
                if any(
                    str(access.get("userId")) == user_id
                    for access in cloud.access.get(device_key, [])
                ):
                    keeps = True
            others = [
                item
                for item in self.hass.data.get(DOMAIN, {}).values()
                if isinstance(item, MirrorCoordinator) and item is not self
            ]
            for other in others:
                if user_id in set(other.cloud_links().values()):
                    keeps = True
                for key in other.guests:
                    if key.isdigit() and other.cloud_user(int(key)) == user_id:
                        keeps = True
            if not keeps:
                await cloud.api.async_delete_guest(cloud.location_id, user_id)
            await self._async_journal_add(
                make_entry(
                    action="cloud_removed",
                    time=dt_util.utcnow().isoformat(),
                    origin=ORIGIN_HA,
                    slot=slot,
                    detail="identity kept (used elsewhere)" if keeps else "identity removed",
                )
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Cloud cleanup of a revoked guest failed: %s", err)

    def cloud_links(self) -> dict[str, str]:
        """The catalog's credential links: ``"<slot>:<type>" -> vendor uuid``."""
        stored = self.entry.options.get("cloud_links")
        if not isinstance(stored, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in stored.items()
            if isinstance(value, str) and value
        }

    def cloud_link(self, slot: int, access_type: str) -> str | None:
        """The vendor uuid linked to this slot's credential, if any.

        For a PIN the guest record is the canonical link; the flat store covers
        the credential types that have no guest record (finger, tag).
        """
        if access_type == "pin" and (user_id := self.cloud_user(slot)):
            return user_id
        return self.cloud_links().get(f"{slot}:{access_type}")

    async def async_set_cloud_link(
        self, slot: int, access_type: str, user_id: str
    ) -> None:
        """Record which vendor identity owns a credential in a slot."""
        links = dict(self.cloud_links())
        links[f"{slot}:{access_type}"] = str(user_id)
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, "cloud_links": links}
        )

    def _cloud_account(self):
        """The cloud coordinator and the vendor device id for this lock."""
        from ..cloud.coordinator import NimlyCloudCoordinator

        mine = str(self.ieee or "").replace(":", "").replace("-", "").lower()
        if len(mine) != 16:
            return None, None
        for item in self.hass.data.get(DOMAIN, {}).values():
            if not isinstance(item, NimlyCloudCoordinator):
                continue
            for device in item.devices:
                if item.device_serial(device.get("id")) == mine:
                    return item, str(device.get("id"))
        return None, None

    def _linked_user_ids(self, access_type: str) -> set[str]:
        """Vendor uuids this lock's catalog already ties to a credential."""
        linked = set(self.cloud_links().values())
        if access_type == "pin":
            # A synced guest's uuid lives on the guest record, not in the flat store.
            for key, guest in self.guests.items():
                if not isinstance(guest, dict) or not key.isdigit():
                    continue
                if user_id := self.cloud_user(int(key)):
                    linked.add(user_id)
        return linked

    def note_simulated_enroll(self) -> None:
        """Mark the next fingerprint enroll as a cloud-side replay."""
        self._replay_enroll_until = time.monotonic() + 120

    async def async_link_cloud_credential(self, slot: int, access_type: str) -> None:
        """Pair a fresh local credential with the cloud access it came from.

        The app creates the access around the push we just handled, so at event
        time exactly one vendor access of that type is usually unlinked. The
        access list is read live — the polled copy can be a minute old. Anything
        ambiguous becomes a repair instead of a guess (docs/cloud-sync.md).
        """
        if self.cloud_link(slot, access_type):
            return
        cloud, device_id = self._cloud_account()
        if cloud is None or device_id is None:
            return
        try:
            accesses = await cloud.api.async_device_access(device_id)
        except Exception:  # noqa: BLE001
            return
        linked = self._linked_user_ids(access_type)
        unlinked = sorted(
            {
                str(access.get("userId"))
                for access in accesses
                if str(access.get("type")) == access_type
                and str(access.get("userId")) not in linked
            }
        )
        if len(unlinked) == 1:
            await self.async_set_cloud_link(slot, access_type, unlinked[0])
            await self.async_journal_note(
                "cloud_linked",
                detail=f"slot {slot} {access_type}",
            )
            ir.async_delete_issue(
                self.hass, DOMAIN, self._issue_id(f"cloud_link_{access_type}")
            )
            return
        if len(unlinked) > 1:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id(f"cloud_link_{access_type}"),
                is_fixable=False,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="cloud_link_ambiguous",
                translation_placeholders={
                    "slot": str(slot),
                    "type": access_type,
                    "count": str(len(unlinked)),
                },
            )

    async def async_create_guest(
        self,
        name: str,
        code: str | None = None,
        slot: int | None = None,
        until: str | None = None,
        one_time: bool = False,
        group: str | None = None,
        permanent: bool = False,
    ) -> dict[str, Any]:
        """Write a guest PIN, name the slot and remember the window.

        The code is returned once so the caller can hand it to the guest; for
        temporary and one-time guests it is never stored or logged anywhere. A
        one-time code is revoked by itself the first time that slot opens the
        door.

        A guest created with ``permanent`` is the exception: the code is kept
        in the entry options (like a recurring guest's), so a cleared slot or a
        lost lock can be replayed with the same digits, and nothing is ever
        cleared - no expiry, no window.
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            raise RuntimeError("a guest name is required")
        normalized_until = normalize_until(until) if until else None
        if permanent:
            # A permanent code is never cleared: expiry and one-time do not apply.
            normalized_until = None
            one_time = False
        elif until and normalized_until is None:
            raise RuntimeError("until is not a valid ISO timestamp")
        if slot is None:
            occupied = {s for s, _data in self.slots.items() if self.slots.occupied(s)}
            occupied |= set(self.slot_map.values())
            occupied |= {int(key) for key in self.guests if str(key).isdigit()}
            slot = pick_slot(
                occupied,
                first_user_slot(self.entry.options),
                pin_capacity(self.lock_facts),
            )
            if slot is None:
                raise RuntimeError("the lock has no free user slot")
        code_text = str(code) if code else generate_code()
        if not valid_code(code_text):
            raise RuntimeError("the code must be 4-8 digits")
        if permanent:
            detail = "permanent"
        elif normalized_until:
            detail = f"until {normalized_until}"
        else:
            detail = "no expiry"
        await self._async_set_pin(
            slot,
            code_text,
            journal={
                "action": GUEST_CREATED,
                "name": clean_name,
                "detail": detail,
            },
        )
        self.slots.set_name(slot, clean_name)
        await self._async_publish_slot(slot, self.slots.occupied(slot))
        record: dict[str, Any] = {
            "name": clean_name,
            "kind": "permanent" if permanent else "simple",
            "one_time": bool(one_time),
            "until": normalized_until,
            "created": dt_util.utcnow().isoformat(),
            **({"group": str(group)} if group else {}),
        }
        if permanent:
            # The deliberate cost of a permanent code: we hold the value, so a
            # cleared slot or a lost lock can be replayed with the same digits.
            record["code"] = code_text
        self.guests[str(slot)] = record
        await self._async_save_guests()
        if normalized_until:
            self._schedule_guest_expiry(slot, normalized_until)
        await self._async_cloud_sync_guest(slot, code_text)
        self._publish_snapshot()
        _LOGGER.info(
            "Guest code created on slot %s (%s)",
            slot,
            "permanent" if permanent else normalized_until or "no expiry",
        )
        return {
            "slot": slot,
            "code": code_text,
            "name": clean_name,
            "until": normalized_until,
            "one_time": bool(one_time),
            "permanent": bool(permanent),
        }

    async def async_create_recurring_guest(
        self,
        name: str,
        code: str | None = None,
        windows: Any = None,
        slot: int | None = None,
        paused: bool = False,
        group: str | None = None,
    ) -> dict[str, Any]:
        """A guest whose code stays the same, valid only inside weekly windows.

        The lock has no schedules, so the window is enforced here: the code is
        written when a window opens and cleared when it closes, and the code
        value is kept in the entry options so the same digits can be restored
        every time. That storage is the deliberate cost of a fixed code.
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            raise RuntimeError("a guest name is required")
        schedule = normalize_windows(windows)
        if schedule is None:
            raise RuntimeError("the schedule needs at least one valid window")
        if slot is None:
            occupied = {s for s, _data in self.slots.items() if self.slots.occupied(s)}
            occupied |= set(self.slot_map.values())
            occupied |= {int(key) for key in self.guests if str(key).isdigit()}
            slot = pick_slot(
                occupied,
                first_user_slot(self.entry.options),
                pin_capacity(self.lock_facts),
            )
            if slot is None:
                raise RuntimeError("the lock has no free user slot")
        code_text = str(code) if code else generate_code()
        if not valid_code(code_text):
            raise RuntimeError("the code must be 4-8 digits")
        self.guests[str(slot)] = {
            "name": clean_name,
            "kind": "recurring",
            "code": code_text,
            "schedule": schedule,
            "paused": bool(paused),
            "created": dt_util.utcnow().isoformat(),
            **({"group": str(group)} if group else {}),
        }
        self.slots.set_name(slot, clean_name)
        await self._async_save_guests()
        await self._apply_guest_state(slot)
        self._schedule_guest_boundary(slot)
        await self._async_cloud_sync_guest(slot, code_text)
        self._publish_snapshot()
        row = self.guest_rows().get(str(slot), {})
        _LOGGER.info(
            "Recurring guest created on slot %s (%s)", slot, schedule_describe(schedule)
        )
        return {
            "slot": slot,
            "code": code_text,
            "name": clean_name,
            "schedule": schedule,
            "paused": bool(paused),
            "in_window": bool(row.get("in_window")),
        }

    async def async_update_guest(self, slot: int, changes: dict[str, Any]) -> dict[str, Any]:
        """Change a guest's name, code, schedule, pause or expiry in one go."""
        key = str(slot)
        guest = self.guests.get(key)
        if guest is None:
            raise RuntimeError(f"no guest on slot {slot}")
        if "name" in changes:
            clean = str(changes.get("name") or "").strip()
            if not clean:
                raise RuntimeError("a guest name is required")
            guest["name"] = clean
            self.slots.set_name(slot, clean)
        if "code" in changes:
            code_text = str(changes.get("code") or "")
            if not valid_code(code_text):
                raise RuntimeError("the code must be 4-8 digits")
            guest["code"] = code_text
            if "pin" in self.slots.credentials(slot):
                await self._async_set_pin(
                    slot,
                    code_text,
                    journal={
                        "action": GUEST_CREATED,
                        "name": guest.get("name"),
                        "detail": "code changed",
                    },
                )
        if "schedule" in changes:
            schedule = normalize_windows(changes.get("schedule"))
            if schedule is None:
                raise RuntimeError("the schedule needs at least one valid window")
            if not valid_code(str(guest.get("code") or "")):
                # A simple guest never stored its code, and every window re-opens
                # with the stored one, so a schedule without a code would write
                # nothing. Make the caller supply it.
                raise RuntimeError(
                    "a recurring guest needs a code; provide one with the schedule"
                )
            guest["kind"] = "recurring"
            guest["schedule"] = schedule
        if "paused" in changes:
            guest["paused"] = bool(changes.get("paused"))
        if "until" in changes:
            normalized = normalize_until(changes.get("until"))
            guest["until"] = normalized
        await self._async_save_guests()
        await self._apply_guest_state(slot)
        self._schedule_guest_boundary(slot)
        self._publish_snapshot()
        if {"name", "until", "code"} & set(changes):
            # The cloud follows a rename, an expiry or a code change; creation
            # and revocation have their own paths.
            await self._async_cloud_push_guest_update(slot, changes)
        return {"slot": slot, "guest": dict(guest)}

    def _guest_boundary_active(self, guest: dict[str, Any]) -> bool:
        """Whether a guest needs boundary timers (recurring and not paused)."""
        return guest.get("kind") == "recurring" and not guest.get("paused")

    async def _apply_guest_state(self, slot: int, now: Any = None) -> None:
        """Make the lock match the schedule right now.

        The table already tracks what the lock holds, so a steady state is a
        no-op: the code is written only when a window is open and the slot has
        nothing, and cleared only when the slot holds something outside its
        window. Clears made through this integration update the table, so the
        startup pass repairs them; a clear made outside it needs a pause/resume
        (or any edit) to be noticed again.
        """
        guest = self.guests.get(str(slot))
        if guest is None or guest.get("kind") != "recurring":
            return
        windows = guest.get("schedule") or []
        current = now or dt_util.now()
        open_now = bool(windows) and not guest.get("paused") and in_window(windows, current)
        has_pin = "pin" in self.slots.credentials(slot)
        if open_now and not has_pin:
            code_text = str(guest.get("code") or "")
            if not valid_code(code_text):
                _LOGGER.error("Recurring guest on slot %s has no usable code", slot)
                return
            await self._async_set_pin(
                slot,
                code_text,
                journal={
                    "action": GUEST_WINDOW_OPEN,
                    "name": guest.get("name"),
                    "detail": schedule_describe(windows),
                },
            )
        elif not open_now and has_pin:
            await self._async_clear_pin(
                slot,
                journal={
                    "action": GUEST_WINDOW_CLOSE,
                    "name": guest.get("name"),
                    "detail": schedule_describe(windows),
                },
            )

    async def _async_guest_boundary(self, slot: int) -> None:
        """A window edge arrived: apply the new state and arm the next edge."""
        await self._apply_guest_state(slot)
        self._schedule_guest_boundary(slot)

    def _schedule_guest_boundary(self, slot: int) -> None:
        """Arm a timer for the next window edge; a missed one fires at startup."""
        self._cancel_guest_timer(slot)
        guest = self.guests.get(str(slot))
        if guest is None or not self._guest_boundary_active(guest):
            return
        windows = guest.get("schedule") or []
        upcoming = next_boundary(windows, dt_util.now())
        if upcoming is None:
            return
        delay = (upcoming - dt_util.utcnow()).total_seconds()
        if delay <= 0:
            self.hass.async_create_task(self._async_guest_boundary(slot))
            return

        async def _edge(_now: Any = None, guest_slot: int = slot) -> None:
            await self._async_guest_boundary(guest_slot)

        self._guest_unsubs[str(slot)] = async_call_later(self.hass, delay, _edge)

    def guest_rows(self) -> dict[str, dict[str, Any]]:
        """Every stored guest with its live state, keyed by slot (for entities)."""
        now = dt_util.now()
        rows: dict[str, dict[str, Any]] = {}
        for key, guest in self.guests.items():
            if not isinstance(guest, dict):
                continue
            kind = guest.get("kind", "simple")
            windows = guest.get("schedule") or []
            cloud_users: list[str] = []
            has_finger = False
            finger_restorable = False
            if key.isdigit():
                slot = int(key)
                if pin_user := self.cloud_user(slot):
                    cloud_users.append(pin_user)
                linked = self.cloud_links()
                cloud_users.extend(
                    user
                    for link_key, user in linked.items()
                    if link_key.startswith(f"{slot}:")
                )
                # A finger can be replayed by reusing the slot, but only when
                # one has really opened the door: an enrollment alone proves
                # nothing about the template the lock holds.
                has_finger = f"{slot}:finger" in linked
                finger_restorable = has_finger and self.slots.finger_confirmed(slot)
            row: dict[str, Any] = {
                "slot": int(key) if key.isdigit() else None,
                "name": guest.get("name"),
                "kind": kind,
                "created": guest.get("created"),
                "has_code": key.isdigit() and "pin" in self.slots.credentials(int(key)),
                "cloud_users": sorted(set(cloud_users)),
                "group": guest.get("group"),
                # True when the catalog holds the PIN value itself, so the code
                # can be replayed after a loss; a temporary guest's code is
                # shown once and never stored.
                "restorable": bool(guest.get("code")),
                "has_finger": has_finger,
                "finger_restorable": finger_restorable,
            }
            if kind == "recurring":
                row["schedule"] = windows
                row["summary"] = schedule_describe(windows)
                row["paused"] = bool(guest.get("paused"))
                row["in_window"] = bool(
                    windows and not guest.get("paused") and in_window(windows, now)
                )
                if guest.get("paused"):
                    row["state"] = "paused"
                else:
                    row["state"] = "active" if row["in_window"] else "outside"
            elif kind == "permanent":
                # Always valid until revoked; the stored code survives a loss.
                row["state"] = "active" if row["has_code"] else "expired"
            else:
                row["until"] = guest.get("until")
                row["one_time"] = bool(guest.get("one_time"))
                row["state"] = (
                    "active"
                    if row["has_code"] and not is_expired(guest.get("until"), now)
                    else "expired"
                )
            rows[str(key)] = row
        return rows

    async def async_revoke_guest(
        self, slot: int, *, reason: str = GUEST_REVOKED
    ) -> dict[str, Any]:
        """Clear a guest code now and forget the window.

        The lock comes first: the guest record is only dropped once the code is
        really gone, so a failing clear cannot leave a lost record and a live
        code behind.
        """
        guest = self.guests.get(str(slot))
        await self._async_clear_pin(
            slot,
            journal={"action": reason, "name": (guest or {}).get("name")},
        )
        revoked = self.guests.pop(str(slot), None)
        self._cancel_guest_timer(slot)
        await self._async_save_guests()
        self.slots.clear(slot)
        # A dead guest's identity links must not outlive it: a future guest in
        # this slot would inherit them and the sync would trust that mapping.
        users = dict(self.entry.options.get("cloud_users") or {})
        links = dict(self.cloud_links())
        dropped = users.pop(str(slot), None)
        linked_users = {
            value
            for key, value in links.items()
            if key.startswith(f"{slot}:")
        }
        if dropped:
            linked_users.add(dropped)
        had_finger = any(
            key.startswith(f"{slot}:finger") for key in links
        )
        for key in [key for key in links if key.startswith(f"{slot}:")]:
            links.pop(key, None)
        if dropped is not None or len(links) != len(self.cloud_links()):
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={**self.entry.options, "cloud_users": users, "cloud_links": links},
            )
        # The person is losing access everywhere: take the fingerprint out of
        # the lock, and have the cloud forget what is now nobody's.
        if had_finger:
            try:
                await self._async_zcl(ZCL_CMD_FP_CLEAR, slot)
                self.slots.mark_fingerprint(slot, False)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Clearing the fingerprint of slot %s failed: %s", slot, err)
        for user_id in sorted(linked_users):
            self.hass.async_create_task(self._async_cloud_remove_guest(slot, user_id))
        stale = [virtual for virtual, real in self.bound.items() if real == slot]
        if stale:
            for virtual in stale:
                self.bound.pop(virtual, None)
            self._save_bound_map()
        await self._async_publish_slot(slot, False)
        self._publish_snapshot()
        return {"slot": slot, "revoked": revoked is not None}

    def _schedule_guest_expiry(self, slot: int, until: str) -> None:
        """Clear the code when the window ends; a missed timer fires at startup."""
        self._cancel_guest_timer(slot)
        when = dt_util.parse_datetime(until)
        if when is None:
            return
        delay = (when - dt_util.utcnow()).total_seconds()
        if delay <= 0:
            self.hass.async_create_task(self._async_expire_guest(slot))
            return

        async def _expire(_now: Any = None, guest_slot: int = slot) -> None:
            try:
                await self._async_expire_guest(guest_slot)
            except Exception as err:  # noqa: BLE001
                # The guest stays and the next start retries; a consumed timer
                # must not hide a live code.
                _LOGGER.warning(
                    "Expiry of guest slot %s failed (retried at next start): %s",
                    guest_slot,
                    err,
                )

        # A coroutine callback: HassJob runs it on the event loop, where a
        # plain lambda would call async_create_task from the wrong thread.
        self._guest_unsubs[str(slot)] = async_call_later(self.hass, delay, _expire)

    def _cancel_guest_timer(self, slot: int) -> None:
        unsub = self._guest_unsubs.pop(str(slot), None)
        if unsub is not None:
            try:
                unsub()
            except Exception:  # noqa: BLE001 - a dead unsubscribe is not an error
                _LOGGER.debug("Guest timer unsubscribe failed", exc_info=True)

    async def _async_expire_guest(self, slot: int) -> None:
        guest = self.guests.get(str(slot))
        if guest is None:
            return
        _LOGGER.info("Guest code on slot %s expired", slot)
        await self.async_revoke_guest(slot, reason=GUEST_EXPIRED)

    async def _async_resume_guests(self) -> None:
        """After a restart: expire what ended, re-apply schedules, re-arm timers."""
        now = dt_util.utcnow()
        for slot in expired_slots(self.guests, now):
            await self._async_expire_guest(slot)
        for key, guest in self.guests.items():
            if not isinstance(guest, dict) or not str(key).isdigit():
                continue
            slot = int(key)
            until = guest.get("until")
            if until:
                self._schedule_guest_expiry(slot, str(until))
            if guest.get("kind") == "recurring":
                # The lock may have gone through window edges while HA was down.
                await self._apply_guest_state(slot)
                self._schedule_guest_boundary(slot)

    # -- OTA ---------------------------------------------------------------

    async def _async_fetch_manifest(self) -> None:
        """Fetches the OTA manifest (version and filename per target)."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(self.ota_manifest_url, timeout=20) as resp:
                if resp.status != 200:
                    _LOGGER.debug("OTA-manifest: HTTP %s", resp.status)
                    return
                self.firmware_manifest = await resp.json(content_type=None)
        except (TimeoutError, ValueError, aiohttp.ClientError) as err:
            _LOGGER.debug("Could not fetch the OTA manifest: %s", err)

    async def async_ota(self, url: str) -> None:
        """Sends an OTA command to the bridge, which downloads it and reboots."""
        if not url:
            raise ValueError("No firmware URL")
        await self._async_publish({"cmd": CMD_OTA, "url": url})
        _LOGGER.info("OTA requested: %s", url)

    async def async_ota_c6(self, url: str, sha256: str, version: str) -> None:
        """Asks the bridge to ferry a firmware image to the C6 emulator over UART."""
        if not url:
            raise ValueError("No firmware URL")
        await self._async_publish(
            {"cmd": "ota_c6", "url": url, "sha256": sha256, "version": version}
        )
        _LOGGER.info("C6 OTA requested: %s (%s)", url, version)

    async def _async_update_data(self) -> dict[str, Any]:
        """Periodic update: manifest plus a sync of battery/volume/auto-lock to the app."

        The sync keeps the app side from staying at the emulator start value after a restart.
        """
        await self._async_fetch_manifest()
        await self._async_sync_to_app()
        return self._snapshot()

    # -- MQTT in (app -> lock) -------------------------------------------------

    @callback
    def _on_bridge_to_ha(self, msg: mqtt.ReceiveMessage) -> None:
        self._last_state_rx = time.monotonic()
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return

        ev = data.get("ev")
        if ev == "hello":
            # The emulator greets: {"ev":"hello","fw":"...","ieee":"00:11:..."}
            if isinstance(data.get("fw"), str):
                self.firmware = data["fw"]
            if isinstance(data.get("ieee"), str):
                self.emulator_ieee = data["ieee"].lower()
                if self.ieee and self.emulator_ieee != self.ieee.lower():
                    _LOGGER.warning(
                        "The emulator IEEE %s does not match the lock %s - provision with "
                        "the nimly.set_ieee service",
                        self.emulator_ieee,
                        self.ieee,
                    )
            # The emulator greets on every health ping. A gap means it was away, so it
            # has rebooted (firmware update, power cycle) and its RAM-backed mirrored
            # settings (auto-lock, volume, battery) are back at their defaults. Re-assert
            # them, or the app loses the auto-lock notifications until the next sync.
            now = time.monotonic()
            if self._last_hello and now - self._last_hello > HELLO_GAP:
                self.hass.async_create_task(self._async_sync_to_app())
            self._last_hello = now
            self._publish_snapshot()
        elif ev == "net" and isinstance(data.get("joined"), bool):
            self.emulator_joined = data["joined"]
            if self.emulator_joined:
                self._not_joined_since = None
                ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("emulator_not_joined"))
            self._publish_snapshot()
        elif ev == EV_ACTION:
            self.hass.async_create_task(self._async_handle_action(data))
        elif ev == EV_FP_ENROLL:
            self.hass.async_create_task(
                self._async_handle_fingerprint(data.get("slot"), enroll=True)
            )
        elif ev == EV_FP_CLEAR:
            self.hass.async_create_task(
                self._async_handle_fingerprint(data.get("slot"), enroll=False)
            )
        elif ev == EV_TAG_SCAN:
            self.hass.async_create_task(
                self._async_handle_tag_scan(int(data.get("arg") or 0))
            )
        elif ev == EV_TAG_CLEAR:
            self.hass.async_create_task(
                self._async_handle_tag_clear(int(data.get("arg") or 0))
            )
        elif ev == EV_VOLUME:
            self._on_app_volume(data.get("value"))
        elif ev == EV_AUTOLOCK:
            self._on_app_autolock(data.get("value"))
        elif "state" in data:
            self._apply_state(data.get("state"))

    @callback
    def _on_state(self, msg: mqtt.ReceiveMessage) -> None:
        self._last_state_rx = time.monotonic()
        self._set_online(True)
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if isinstance(data, dict):
            self._apply_state(data.get("lock"))

    @callback
    def _on_battery(self, msg: mqtt.ReceiveMessage) -> None:
        self._last_state_rx = time.monotonic()
        self._set_online(True)
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if isinstance(data, dict) and isinstance(data.get("battery"), (int, float)):
            self.app_battery = int(data["battery"])
            self._publish_snapshot()

    @callback
    def _on_bridge_info(self, msg: mqtt.ReceiveMessage) -> None:
        """The bridge retained identity (nimly/info): prefix, id and firmware."""
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        self.bridge_info = data
        self._last_state_rx = time.monotonic()
        self._set_online(True)
        prefix = data.get("prefix")
        if isinstance(prefix, str) and prefix.rstrip("/") != self.prefix:
            _LOGGER.warning(
                "The bridge announces prefix %s but %s is configured",
                prefix,
                self.prefix,
            )
        self._publish_snapshot()

    @callback
    def _on_ota(self, msg: mqtt.ReceiveMessage) -> None:
        """OTA status from the bridge (downloading/done/error)."""
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if isinstance(data, dict):
            self.ota_status = data
            self._publish_snapshot()

    @callback
    def _on_pin(self, msg: mqtt.ReceiveMessage) -> None:
        self._last_state_rx = time.monotonic()
        self._set_online(True)
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        self.hass.async_create_task(self._async_handle_pin(data))

    @callback
    def _on_app_volume(self, value: Any) -> None:
        """The vendor app changed the module's sound volume; follow on the real lock."""
        volume = _as_int(value, -1)
        if volume < 0:
            return
        self.app_volume = volume
        if self.active(CH_VOLUME) and (entity_id := self.related.get("volume")):
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": volume},
                    blocking=False,
                )
            )
        self._publish_snapshot()

    @callback
    def _on_app_autolock(self, value: Any) -> None:
        """The vendor app changed auto-lock on the module; follow on the real lock."""
        seconds = _as_int(value, -1)
        if seconds < 0:
            return
        self.app_autolock = seconds > 0
        if self.active(CH_AUTOLOCK) and (entity_id := self.related.get("autolock")):
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "switch",
                    "turn_on" if self.app_autolock else "turn_off",
                    {"entity_id": entity_id},
                    blocking=False,
                )
            )
        self._publish_snapshot()

    # -- state ------------------------------------------------------------------

    def _apply_state(self, value: Any) -> None:
        if value == "locked":
            self.app_locked = True
        elif value == "unlocked":
            self.app_locked = False
        elif value == "unknown":
            self._set_online(False)
            return
        else:
            return
        self._publish_snapshot()

    def _set_online(self, online: bool) -> None:
        if self.bridge_online != online:
            self.bridge_online = online
            self._publish_snapshot()
            if online:
                # Back after an outage: the emulator may have rebooted in the meantime.
                self.hass.async_create_task(self._async_sync_to_app())

    # -- app -> lock ------------------------------------------------------------

    async def _async_handle_action(self, data: dict[str, Any]) -> None:
        action = data.get("action")
        if action not in (ACTION_LOCK, ACTION_UNLOCK):
            return
        src = data.get("source")
        self.last_event = {
            "action": ACTION_NAMES.get(action),
            "source": SOURCE_NAMES.get(src) if isinstance(src, int) else None,
            "slot": data.get("slot"),
            "time": dt_util.utcnow().isoformat(),
            "direction": "app->lock",
        }
        self.counters["events"] += 1
        self._publish_snapshot()

        if not self.active(CH_LOCK):
            return
        await self._async_command_lock(action == ACTION_LOCK, echo=True)

    async def _async_handle_pin(self, data: dict[str, Any]) -> None:
        if not self.active(CH_PIN):
            return
        ev = data.get("ev")
        virtual = data.get("slot")
        if not isinstance(virtual, int):
            return
        try:
            if ev == EV_PIN_SET and data.get("code"):
                await self._async_app_pin_set(virtual, str(data["code"]))
            elif ev == EV_PIN_CLEAR:
                await self._async_app_pin_clear(virtual)
        except Exception as err:  # noqa: BLE001
            self.counters["errors"] += 1
            self.last_error = str(err)
            _LOGGER.error("PIN mirroring failed (slot %s): %s", virtual, err)
        self.counters["app_to_lock"] += 1
        self._publish_snapshot()

    async def _async_app_pin_set(self, virtual: int, code: str) -> None:
        """Store an app-provisioned PIN, relocating it when it would collide.

        The app is never refused: it reports success to the user before the
        bridge even answers, so a refusal would only diverge in silence. A
        collision with a local credential becomes a virtual -> real mapping,
        and the lock's own events are translated back for attribution.
        """
        bound = code_owner(self.guests, code)
        if bound is not None:
            # The cloud pushed a code we already hold (a synced local guest): bind
            # the vendor slot to the slot the guest already lives in instead of
            # writing a second copy of the same code. A bind is not ownership:
            # the real slot stays ours, so the guest's window and revoke keep
            # working, and an app-side clear cannot delete our credential.
            self.bound[virtual] = bound
            self._save_bound_map()
            await self._async_journal_add(
                make_entry(
                    action="slot_bound",
                    time=dt_util.utcnow().isoformat(),
                    origin=ORIGIN_HA,
                    slot=virtual,
                    detail=f"the code is the local guest in slot {bound}",
                )
            )
            self._publish_snapshot()
            return
        rebind = self.bound.get(virtual)
        if rebind is not None:
            guest = self.guests.get(str(rebind))
            if guest is not None:
                # The app rewrote the access we bound to this guest: keep one
                # credential per person — write the new value into the guest's
                # own slot and teach the catalog, instead of growing a twin.
                await self._async_set_pin(rebind, code, virtual_slot=virtual)
                guest["code"] = code
                await self._async_save_guests()
                await self.async_link_cloud_credential(rebind, "pin")
                await self._async_journal_add(
                    make_entry(
                        action="slot_rebound",
                        time=dt_util.utcnow().isoformat(),
                        origin=ORIGIN_HA,
                        slot=virtual,
                        detail=f"slot {rebind} updated from the app",
                    )
                )
                self._publish_snapshot()
                return
            self.bound.pop(virtual, None)
            self._save_bound_map()

        floor, capacity = self._slot_bounds()
        outcome, real, reason = resolve_write(
            virtual,
            mapping=self.slot_map,
            local_pins=self._local_pin_slots(),
            floor=floor,
            capacity=capacity,
        )
        if outcome == BLOCKED or real is None:
            await self._async_slot_conflict(virtual, reason or "no free slot")
            return
        label = f"pin (app slot {virtual})" if real != virtual else "pin"
        self._flag_new_slot(real, label)
        await self._async_set_pin(real, code, virtual_slot=virtual)
        if outcome in (MOVE, PASS):
            # Remember that the app owns this real slot: the relocation when it
            # collided, an identity mapping when it passed straight through. The
            # app's later edits and clears must resolve to the same slot, and the
            # local side must never touch it.
            self.slot_map[virtual] = real
            self._save_slot_map()
        if outcome == MOVE:
            await self._async_journal_add(
                make_entry(
                    action="slot_relocated",
                    time=dt_util.utcnow().isoformat(),
                    origin=ORIGIN_HA,
                    slot=virtual,
                    detail=f"stored in local slot {real}",
                )
            )
        await self.async_link_cloud_credential(real, "pin")
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(f"slot_conflict_{virtual}"))

    async def _async_app_pin_clear(self, virtual: int) -> None:
        """Apply an app clear to the app's own credential only."""
        outcome, real = resolve_clear(
            virtual, mapping=self.slot_map, local_pins=self._local_pin_slots()
        )
        if outcome == CLEAR and real is not None:
            await self._async_clear_pin(real, virtual_slot=virtual)
            self.slot_map.pop(virtual, None)
            self._save_slot_map()
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(f"slot_conflict_{virtual}"))
        elif outcome == IGNORE:
            await self._async_slot_conflict(virtual, "the slot holds a local credential")
        else:
            # Nothing of the app's lives here; repeat the local truth so the
            # gateway does not allocate around a slot the app has freed.
            await self._async_publish_slot(virtual, self.slots.occupied(virtual))
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(f"slot_conflict_{virtual}"))

    async def _async_handle_fingerprint(self, slot: Any, *, enroll: bool) -> None:
        if not self.active(CH_FINGERPRINT):
            return
        if not isinstance(slot, int):
            return
        if enroll:
            replay = self._replay_enroll_until > time.monotonic()
            self._replay_enroll_until = 0.0
            if replay:
                # A cloud replay of an enroll the lock already holds: record the
                # catalog link, but do not light the reader for it.
                _LOGGER.info(
                    "Fingerprint enroll (slot %s) is a cloud replay; not mirrored",
                    slot,
                )
                await self.async_link_cloud_credential(slot, "finger")
                self._publish_snapshot()
                return
            self._flag_new_slot(slot, "fingerprint")
            # The catalog link does not depend on the physical mirroring below,
            # which can fail on its own; link first so nothing is lost.
            await self.async_link_cloud_credential(slot, "finger")
        command = ZCL_CMD_FP_ENROLL if enroll else ZCL_CMD_FP_CLEAR
        try:
            await self._async_zcl(command, slot)
            # A clear really removes the template; an enrollment proves nothing —
            # the lock reports nothing while it runs, so the app's "Done" (and the
            # slot's own table, or a later usage event) is the only evidence.
            if not enroll:
                self.slots.mark_fingerprint(slot, False)
            await self._async_publish_slot(slot, self.slots.occupied(slot))
        except Exception as err:  # noqa: BLE001
            self.counters["errors"] += 1
            self.last_error = str(err)
            _LOGGER.error(
                "Fingerprint mirroring failed (%s slot %s): %s",
                "enroll" if enroll else "clear",
                slot,
                err,
            )
        self.counters["app_to_lock"] += 1
        self._publish_snapshot()

    # -- lock -> app ------------------------------------------------------------

    @callback
    def _on_tracked_change(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (None, "unknown", "unavailable"):
            return
        if entity_id == self.lock_entity_id:
            self.hass.async_create_task(self._async_lock_to_app(new_state.state))
        elif entity_id == self.related.get("volume") and self.active(CH_VOLUME):
            self.hass.async_create_task(
                self._async_publish({"cmd": CMD_VOLUME, "value": _as_int(new_state.state, 2)})
            )
        elif entity_id == self.related.get("autolock") and self.active(CH_AUTOLOCK):
            self.hass.async_create_task(
                self._async_publish(
                    {"cmd": CMD_AUTOLOCK, "value": 1 if new_state.state == "on" else 0}
                )
            )
        elif entity_id == self.related.get("battery") and self.active(CH_BATTERY):
            self.hass.async_create_task(
                self._async_publish(
                    {"cmd": CMD_BATTERY, "value": _as_int(new_state.state, 100)}
                )
            )

    async def _async_lock_to_app(self, state: str) -> None:
        if state not in ("locked", "unlocked"):
            return
        locked = state == "locked"
        # Echo: if we ordered this state ourselves recently, do not mirror it back.
        now = time.monotonic()
        while self._commanded:
            commanded, deadline = self._commanded[0]
            if now > deadline:
                self._commanded.popleft()
                continue
            if commanded == locked:
                self._commanded.popleft()
                self.app_locked = locked
                self._publish_snapshot()
                return
            break
        if not self.active(CH_LOCK):
            return
        self.counters["lock_to_app"] += 1
        await self._async_publish({"cmd": CMD_LOCK if locked else CMD_UNLOCK})

    @callback
    def _on_lock_activity(self, data: dict[str, Any]) -> None:
        """A decoded operation event from the lock -> app notification."""
        if not self.active(CH_ACTIVITY):
            return
        action = data.get("action_code")
        source = data.get("source_code")
        if not isinstance(action, int):
            return
        slot = data.get("user_slot")
        # The credential may live in a relocated slot, or in the guest's own
        # slot as a bind; the app only knows its own number, so events are
        # translated back before they leave for the bridge.
        shown = None
        if isinstance(slot, int):
            shown = self._virtual_slot(slot)
            if shown is None:
                shown = virtual_of(slot, self.bound)
        if not isinstance(shown, int):
            shown = slot
        master = bool(data.get("master"))
        if (
            isinstance(slot, int)
            and slot > 0
            and source in (
                SRC_KEYPAD,
                SRC_FINGERPRINT,
                SRC_RFID,
            )
        ):
            # Learn which credential type the slot holds, and ask once for a name
            # if it has none, so attribution stays local.
            if source == SRC_KEYPAD:
                self.slots.mark_credential(slot, "pin")
            elif source == SRC_FINGERPRINT:
                self.slots.mark_credential(slot, "fingerprint")
            else:
                self.slots.mark_credential(slot, "rfid")
            label = SOURCE_NAMES.get(source, "credential")
            if shown != slot:
                label = f"{label} (app slot {shown})"
            self._flag_new_slot(slot, label)
            guest = self.guests.get(str(slot))
            if isinstance(guest, dict) and guest.get("one_time"):
                # A one-time code has done its job: revoke it after this use.
                self.hass.async_create_task(
                    self.async_revoke_guest(slot, reason=GUEST_USED)
                )
        # System locks (auto) need no notification - mirrored anyway for consistency.
        if source is not None and source not in HUMAN_SOURCES:
            _LOGGER.debug("Skipping system event source=%s", source)
        if master:
            slot_name = self.slots.name(0, fallback=False) or "Master"
        elif isinstance(slot, int):
            slot_name = self.slots.name(slot)
        else:
            slot_name = None
        self.last_event = {
            "action": ACTION_NAMES.get(action),
            "source": SOURCE_NAMES.get(source) if isinstance(source, int) else None,
            "slot": shown,
            "name": slot_name,
            "time": dt_util.utcnow().isoformat(),
            "direction": "lock->app",
        }
        self.counters["events"] += 1
        self._schedule_facts_refresh()
        self._publish_snapshot()
        self.hass.async_create_task(
            self._async_journal_add(
                make_entry(
                    action=ACTION_NAMES.get(action) or "unknown",
                    time=str(self.last_event.get("time")),
                    origin=ORIGIN_LOCK,
                    source=SOURCE_NAMES.get(source)
                    if isinstance(source, int)
                    else None,
                    slot=shown,
                    name=slot_name,
                )
            )
        )
        self.hass.async_create_task(
            self._async_publish(
                {
                    "cmd": CMD_EVENT,
                    "action": int(action),
                    "source": int(source) if source is not None else 0,
                    "slot": _as_int(shown, 0),
                }
            )
        )

    @callback
    def _on_cloud_activity(self, event: Event) -> None:
        """Cloud context for this lock: who and how, when Zigbee cannot say.

        Only attributed activities are used — an unnamed event would overwrite better
        local data with the vendor's narration. Nothing is published to the bridge:
        the cloud event originated from this lock's own side, so echoing it back
        would make a cloud -> emulator -> cloud loop.
        """
        data = event.data
        serial = str(data.get("serial") or "").replace(":", "").replace("-", "").lower()
        if not self.ieee or serial != self.ieee.replace(":", "").replace("-", "").lower():
            return
        # Any activity for this lock proves the cloud feedback path is alive,
        # named or not, and even when this channel is off - the watchdog that
        # clears a stale-feedback repair depends on it.
        self._cloud_seen_at = time.monotonic()
        if not self.active(CH_ACTIVITY):
            return
        name = data.get("user_name")
        if not name:
            return

        slot = data.get("slot")
        if not isinstance(slot, int):
            slot = self._slot_from_name(name)
        self.last_event = {
            "action": data.get("action"),
            "source": data.get("source"),
            "slot": slot,
            "name": name,
            "user_id": data.get("user_id"),
            "time": dt_util.utcnow().isoformat(),
            "vendor_time": data.get("time") or data.get("vendor_time"),
            "direction": "cloud",
        }
        self.counters["events"] += 1
        self._publish_snapshot()
        self.hass.async_create_task(
            self._async_journal_add(
                make_entry(
                    action=str(data.get("action") or "unknown"),
                    time=str(
                        data.get("time")
                        or data.get("vendor_time")
                        or dt_util.utcnow().isoformat()
                    ),
                    origin=ORIGIN_CLOUD,
                    source=str(data.get("source")) if data.get("source") else None,
                    slot=slot,
                    name=name,
                )
            )
        )

    def _slot_from_name(self, name: Any) -> int | None:
        """The local slot whose name matches a cloud user name, when unambiguous.

        The cloud reports full names while a lock slot holds exactly what the user
        typed, so a unique first-name prefix also counts. Anything ambiguous yields
        None rather than a guess.
        """
        if not isinstance(name, str) or not name.strip():
            return None
        target = name.strip().casefold()
        exact: list[int] = []
        partial: list[int] = []
        for slot, data in self.slots.items():
            local = str(data.get("name") or "").strip().casefold()
            if not local:
                continue
            if local == target:
                exact.append(slot)
            elif target.startswith(local) and target[len(local)] in (" ", "-", "_"):
                partial.append(slot)
        if len(exact) == 1:
            return exact[0]
        if not exact and len(partial) == 1:
            return partial[0]
        return None

    # -- commands to the lock ---------------------------------------------------

    async def async_command_lock(self, locked: bool) -> None:
        """Called by the app mirror entity (HA -> both sides)."""
        await self._async_publish({"cmd": CMD_LOCK if locked else CMD_UNLOCK})
        self.app_locked = locked
        self._publish_snapshot()
        if self.active(CH_LOCK):
            await self._async_command_lock(locked, echo=True)

    async def _async_command_lock(self, locked: bool, *, echo: bool) -> None:
        if echo:
            self._commanded.append((locked, time.monotonic() + ECHO_WINDOW))
        await self.hass.services.async_call(
            "lock",
            "lock" if locked else "unlock",
            {"entity_id": self.lock_entity_id},
            blocking=False,
        )

    async def async_set_volume(self, value: int) -> None:
        if self.active(CH_VOLUME) and (ent := self.related.get("volume")):
            await self.hass.services.async_call(
                "number", "set_value", {"entity_id": ent, "value": value}, blocking=False
            )

    async def async_set_autolock(self, enabled: bool) -> None:
        if self.active(CH_AUTOLOCK) and (ent := self.related.get("autolock")):
            await self.hass.services.async_call(
                "switch",
                "turn_on" if enabled else "turn_off",
                {"entity_id": ent},
                blocking=False,
            )

    async def async_sync(self) -> None:
        await self._async_sync_to_app()

    async def async_provision_ieee(self, ieee: str | None = None) -> None:
        """Provisions the emulator with an IEEE address (that device reboots).

        The default is the lock's own IEEE (the real module's), because the Nimly cloud
        only accepts the addresses of known modules.
        """
        target = (ieee or self.ieee or "").lower()
        if not target:
            raise ValueError("No IEEE given and none found on the lock")
        await self._async_publish({"cmd": "set_ieee", "value": target})
        _LOGGER.info("Provisioning the emulator with IEEE %s", target)

    async def async_set_channel(self, key: str, enabled: bool) -> None:
        """Turns a mirror channel on or off (from the channel switch) and stores it."""
        self.channels[key] = enabled
        self._persist_options()
        self._publish_snapshot()

    async def async_set_master(self, enabled: bool) -> None:
        """Master on/off for the whole mirror."""
        self.master_enabled = enabled
        self._persist_options()
        if enabled:
            await self._async_sync_to_app()
        self._publish_snapshot()

    @callback
    def _persist_options(self) -> None:
        options = dict(self.entry.options)
        options[CONF_CHANNELS] = dict(self.channels)
        options[CONF_ENABLED] = self.master_enabled
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    async def _async_sync_to_app(self) -> None:
        await self._async_heal_app_settings()
        if not self.active(CH_SYNC):
            return
        if self.active(CH_VOLUME) and (
            ent := self.related.get("volume")
        ) and (state := self.hass.states.get(ent)):
            self.app_volume = _as_int(state.state, 2)
            await self._async_publish({"cmd": CMD_VOLUME, "value": self.app_volume})
        if self.active(CH_AUTOLOCK) and (
            ent := self.related.get("autolock")
        ) and (state := self.hass.states.get(ent)):
            self.app_autolock = state.state == "on"
            await self._async_publish(
                {"cmd": CMD_AUTOLOCK, "value": 1 if self.app_autolock else 0}
            )
        if self.active(CH_BATTERY) and (
            ent := self.related.get("battery")
        ) and (state := self.hass.states.get(ent)):
            self.app_battery = _as_int(state.state, 100)
            await self._async_publish({"cmd": CMD_BATTERY, "value": self.app_battery})
        self._publish_snapshot()

    # -- PIN / fingerprint towards ZHA ---------------------------------------

    async def _async_set_pin(
        self,
        slot: int,
        code: str,
        *,
        journal: dict[str, Any] | None = None,
        virtual_slot: int | None = None,
    ) -> None:
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        reason = check_credential_slot(slot, self.entry.options, self.lock_facts)
        if reason is not None:
            raise RuntimeError(reason)
        if virtual_slot is None and (owner := self._virtual_slot(slot)) is not None:
            where = (
                f"the app's slot {owner} credential"
                if owner != slot
                else "the app's credential"
            )
            raise RuntimeError(f"slot {slot} holds {where}; choose another slot")
        if not await self.zha.set_pin(slot, code):
            raise RuntimeError("the lock did not accept the PIN command")
        self.slots.mark_pin(slot, True)
        shown = virtual_slot if virtual_slot is not None else slot
        await self._async_publish_slot(shown, self.slots.occupied(slot))
        entry = make_entry(
            time=dt_util.utcnow().isoformat(),
            origin=ORIGIN_HA,
            slot=shown,
            **(
                journal
                or {
                    "action": "pin_set",
                    "name": self.slots.name(slot, fallback=False) or None,
                }
            ),
        )
        if shown != slot and not entry.get("detail"):
            entry["detail"] = f"stored in local slot {slot}"
        await self._async_journal_add(entry)

    async def _async_clear_pin(
        self,
        slot: int,
        *,
        journal: dict[str, Any] | None = None,
        virtual_slot: int | None = None,
    ) -> None:
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        reason = check_credential_slot(slot, self.entry.options, self.lock_facts)
        if reason is not None:
            raise RuntimeError(reason)
        if virtual_slot is None and (owner := self._virtual_slot(slot)) is not None:
            where = (
                f"the app's slot {owner} credential"
                if owner != slot
                else "the app's credential"
            )
            raise RuntimeError(
                f"slot {slot} holds {where}; clear that in the app instead"
            )
        if not await self.zha.clear_pin(slot):
            raise RuntimeError("the lock did not accept the clear command")
        self.slots.mark_pin(slot, False)
        shown = virtual_slot if virtual_slot is not None else slot
        await self._async_publish_slot(shown, self.slots.occupied(slot))
        entry = make_entry(
            time=dt_util.utcnow().isoformat(),
            origin=ORIGIN_HA,
            slot=shown,
            **(journal or {"action": "pin_cleared"}),
        )
        if shown != slot and not entry.get("detail"):
            entry["detail"] = f"cleared local slot {slot}"
        await self._async_journal_add(entry)

    async def async_forget_zigbee_network(self) -> None:
        """Ask the emulator to drop its Zigbee state and start as a fresh module.

        The vendor hub refuses a device it no longer knows, and a module that
        still holds the old network cannot fall back to a fresh join on its own
        — so a repair resets the module first. The UART/MQTT identity (the C3
        link, the emulator's role) is untouched.
        """
        await self._async_publish({"cmd": CMD_FACTORY_RESET})
        await self._async_journal_add(
            make_entry(
                action="zigbee_reset",
                time=dt_util.utcnow().isoformat(),
                origin=ORIGIN_HA,
                detail="the emulator starts fresh for a repair",
            )
        )
        self._publish_snapshot()

    async def async_start_finger_enroll(self, slot: int, mode: str = "auto") -> dict[str, Any]:
        """Start a fingerprint enrollment for one slot.

        The lock reports nothing while an enrollment runs — a template exists
        only once someone has really opened the door with that finger — so this
        lights the reader and leaves the touch to the person at the door.
        ``mode`` is ``auto`` (through the cloud when the slot's guest is synced,
        so the app records the access too), ``cloud`` or ``local``.
        """
        if not self.active(CH_FINGERPRINT):
            raise RuntimeError("fingerprint mirroring is off")
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        guest = self.guests.get(str(slot))
        name = (guest or {}).get("name") or self.slots.name(slot, fallback=False)

        user_id = self.cloud_user(slot)
        use_cloud = False
        if mode in ("auto", "cloud") and user_id and self.active(CH_CLOUD):
            use_cloud = True
        if mode == "cloud" and not user_id:
            raise RuntimeError(f"slot {slot} has no cloud identity to enroll through")

        if use_cloud:
            enrolled = await self._async_cloud_enroll_finger(user_id)
            if enrolled:
                await self._async_journal_add(
                    make_entry(
                        action="finger_enroll_started",
                        time=dt_util.utcnow().isoformat(),
                        origin=ORIGIN_HA,
                        slot=slot,
                        name=name,
                        detail="via the cloud",
                    )
                )
                self._publish_snapshot()
                return {"slot": slot, "name": name, "via": "cloud"}
            if mode == "cloud":
                raise RuntimeError("the cloud did not accept the enrollment")

        await self._async_zcl(ZCL_CMD_FP_ENROLL, slot)
        await self._async_journal_add(
            make_entry(
                action="finger_enroll_started",
                time=dt_util.utcnow().isoformat(),
                origin=ORIGIN_HA,
                slot=slot,
                name=name,
                detail="local",
            )
        )
        await self._async_publish_slot(slot, self.slots.occupied(slot))
        self._publish_snapshot()
        return {"slot": slot, "name": name, "via": "local"}

    async def _async_handle_tag_scan(self, arg: int) -> None:
        """The app asked for a tag scan; make the real lock read the tag.

        Command 0x70 is new (found 2026-09-23): the reader is lit on the real
        lock, and the whole reply is journaled — its shape is what the module
        must eventually ferry back to the vendor so the cloud can record the
        tag's value.
        """
        if self.zha is None:
            return
        result = await self.zha.send_vendor_detailed(ZCL_CMD_TAG_SCAN, arg)
        await self._async_journal_add(
            make_entry(
                action="tag_scan",
                time=dt_util.utcnow().isoformat(),
                origin=ORIGIN_HA,
                detail=(
                    f"arg 0x{arg:04x} · svar {result.get('reply')}"
                    if result.get("ok")
                    else f"arg 0x{arg:04x} · fel {result.get('error')}"
                ),
            )
        )
        self._publish_snapshot()

    async def _async_handle_tag_clear(self, arg: int) -> None:
        """The app removed a credential (0x18); do the same on the real lock.

        The id is the vendor's credential handle from the enroll flow, and the
        real module answers the same command natively — the reply is journaled
        so the ferry back can be built from measured bytes (docs/protocol.md).
        """
        if self.zha is None:
            return
        result = await self.zha.send_vendor_detailed(ZCL_CMD_TAG_CLEAR, arg)
        await self._async_journal_add(
            make_entry(
                action="tag_clear",
                time=dt_util.utcnow().isoformat(),
                origin=ORIGIN_HA,
                detail=(
                    f"arg 0x{arg:04x} · svar {result.get('reply')}"
                    if result.get("ok")
                    else f"arg 0x{arg:04x} · fel {result.get('error')}"
                ),
            )
        )
        self._publish_snapshot()

    async def _async_cloud_enroll_finger(self, user_id: str) -> bool:
        """Ask the cloud to enroll one of its users' fingers.

        The vendor pushes the enrollment to the module (us); the push lights the
        lock's reader through the ordinary fingerprint path, so a person still
        has to touch it for a template to exist.
        """
        from ..cloud.coordinator import NimlyCloudCoordinator
        from ..cloud.sync import cloud_device_for

        clouds = [
            item
            for item in self.hass.data.get(DOMAIN, {}).values()
            if isinstance(item, NimlyCloudCoordinator)
        ]
        if not clouds:
            return False
        cloud = clouds[0]
        device_id = cloud_device_for(cloud, self)
        if device_id is None:
            return False
        try:
            await cloud.api.async_enroll_finger(device_id, user_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Cloud finger enrollment failed: %s", err)
            return False
        return True

    async def _async_zcl(self, command: int, arg: int) -> None:
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        if not await self.zha.send_fingerprint(command, arg):
            raise RuntimeError(f"the lock did not accept command 0x{command:02x}")

    # -- health -------------------------------------------------------------

    async def _async_health(self, _now: Any = None) -> None:
        if self.zha is not None:
            self.zha.ensure_listener()
        await self._async_publish({"cmd": CMD_GET_STATE})
        if self._last_state_rx and (
            time.monotonic() - self._last_state_rx > HEALTH_TIMEOUT
        ):
            self._set_online(False)
        self._check_health_issues()

    @callback
    def _check_health_issues(self) -> None:
        """Surface the two silent failure modes as repairs."""
        now = time.monotonic()

        # 1) The emulator answers on the UART but is not on a Zigbee network.
        if self.emulator_joined is False:
            if self._not_joined_since is None:
                self._not_joined_since = now
            elif now - self._not_joined_since > 300:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    self._issue_id("emulator_not_joined"),
                    is_fixable=True,
                    is_persistent=True,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="emulator_not_joined",
                    data={"entry_id": self.entry.entry_id},
                )
        elif self.emulator_joined is True:
            self._not_joined_since = None
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("emulator_not_joined"))

        # 2) We sent the vendor cloud something that should come back in its feed;
        #    if it never does, the bridge's cloud link is likely wedged.
        if self._cloud_expect_after is not None:
            if self._cloud_seen_at >= self._cloud_expect_after:
                self._cloud_expect_after = None
                ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("cloud_feedback_stale"))
            elif (
                now - self._cloud_expect_after > 180
                and self._cloud_entry_loaded()
            ):
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    self._issue_id("cloud_feedback_stale"),
                    is_fixable=False,
                    is_persistent=True,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="cloud_feedback_stale",
                )

    def _cloud_entry_loaded(self) -> bool:
        return any(
            entry.data.get(CONF_TYPE) == TYPE_CLOUD
            and entry.state is ConfigEntryState.LOADED
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default
