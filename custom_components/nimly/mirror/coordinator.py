"""The mirror engine — the emulator (app side) against the real lock.

Owns the app side (the emulator on the Nimly bridge) and mirrors it against the real
- app -> lock:  lock/unlock, PIN, fingerprint
- lock -> app:  lock/unlock, volume, auto-lock, battery, notifications (action/source/slot)

Everything happens in code: the user writes no automations and no YAML.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import timedelta
from typing import Any, Callable

import aiohttp

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
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
    CH_FINGERPRINT,
    CH_LOCK,
    CH_NAMES,
    CH_PIN,
    CH_RECONCILE,
    CH_SYNC,
    CH_VOLUME,
    CMD_AUTOLOCK,
    CMD_BATTERY,
    CMD_EVENT,
    CMD_GET_STATE,
    CMD_LOCK,
    CMD_OTA,
    CMD_UNLOCK,
    CMD_VOLUME,
    CONF_CHANNELS,
    CONF_ENABLED,
    CONF_OTA_MANIFEST_URL,
    DEFAULT_ENDPOINT,
    DEFAULT_OTA_MANIFEST_URL,
    DOMAIN,
    ECHO_WINDOW,
    EVENT_NIMLY_CLOUD_ACTIVITY,
    EV_ACTION,
    EV_FP_CLEAR,
    EV_FP_ENROLL,
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
    ZCL_CMD_FP_CLEAR,
    ZCL_CMD_FP_ENROLL,
)

from .slots import SlotTable
from .zha_link import ZhaLink

_LOGGER = logging.getLogger(__name__)


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
        self.last_pin: dict[str, Any] | None = None
        self.slots = SlotTable(hass, entry)
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

        # HA -> app: the slot table's occupancy goes to the app, so a slot freed
        # in HA frees in the app too.
        if self.active(CH_PIN):
            self.hass.async_create_task(self._async_sync_slots())
        if self.zha is not None:
            self.zha.ensure_listener()
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._async_health, timedelta(seconds=HEALTH_INTERVAL)
            )
        )

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
            "battery": self.app_battery,
            "volume": self.app_volume,
            "autolock": self.app_autolock,
            "last_event": self.last_event,
            "last_pin": self.last_pin,
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
        }

    @callback
    def _publish_snapshot(self) -> None:
        self.async_set_updated_data(self._snapshot())

    async def _async_sync_slots(self) -> None:
        for slot, _data in self.slots.items():
            await self._async_publish_slot(slot, self.slots.occupied(slot))

    async def _async_publish_slot(self, slot: int, occupied: bool) -> None:
        await self._async_publish({"cmd": "pin_status", "slot": slot, "set": occupied})

    def _slot_is_named(self, slot: int) -> bool:
        return bool(self.slots.get(slot).get("name"))

    @callback
    def _flag_new_slot(self, slot: int, kind: str) -> None:
        """Raise a fixable repair when the app creates a slot we have no name for."""
        if slot < 3 or not self.active(CH_PIN) or self._slot_is_named(slot):
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"new_slot_{slot}",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="new_slot",
            translation_placeholders={"slot": str(slot), "kind": kind},
        )

    async def async_set_slot_name(self, slot: int, name: str) -> None:
        """Write a slot name into the table and clear the repair."""
        self.slots.set_name(slot, name)
        ir.async_delete_issue(self.hass, DOMAIN, f"new_slot_{slot}")
        await self._async_publish_slot(slot, self.slots.occupied(slot))
        _LOGGER.info("Named slot %s as %s", slot, name)

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
        if ev == EV_PIN_SET and isinstance(data.get("slot"), int):
            self._flag_new_slot(data["slot"], "pin")
        slot = data.get("slot")
        if not isinstance(slot, int):
            return
        try:
            if ev == EV_PIN_SET and data.get("code"):
                await self._async_set_pin(slot, str(data["code"]))
            elif ev == EV_PIN_CLEAR:
                await self._async_clear_pin(slot)
        except Exception as err:  # noqa: BLE001
            self.counters["errors"] += 1
            self.last_error = str(err)
            _LOGGER.error("PIN mirroring failed (slot %s): %s", slot, err)
        self.counters["app_to_lock"] += 1
        self._publish_snapshot()

    async def _async_handle_fingerprint(self, slot: Any, *, enroll: bool) -> None:
        if not self.active(CH_FINGERPRINT):
            return
        if not isinstance(slot, int):
            return
        if enroll:
            self._flag_new_slot(slot, "fingerprint")
        command = ZCL_CMD_FP_ENROLL if enroll else ZCL_CMD_FP_CLEAR
        try:
            await self._async_zcl(command, slot)
            self.slots.mark_rfid(slot, enroll)
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
        old_state = event.data.get("old_state")
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
        if isinstance(slot, int) and source in (
            SRC_KEYPAD,
            SRC_FINGERPRINT,
            SRC_RFID,
        ):
            # A person used a credential we cannot name yet: ask once, so the next
            # event carries a name without depending on the cloud.
            self._flag_new_slot(slot, SOURCE_NAMES.get(source, "credential"))
        # System locks (auto) need no notification - mirrored anyway for consistency.
        if source is not None and source not in HUMAN_SOURCES:
            _LOGGER.debug("Skipping system event source=%s", source)
        self.last_event = {
            "action": ACTION_NAMES.get(action),
            "source": SOURCE_NAMES.get(source) if isinstance(source, int) else None,
            "slot": slot,
            "name": self.slots.name(slot) if isinstance(slot, int) else None,
            "time": dt_util.utcnow().isoformat(),
            "direction": "lock->app",
        }
        self.counters["events"] += 1
        self._publish_snapshot()
        self.hass.async_create_task(
            self._async_publish(
                {
                    "cmd": CMD_EVENT,
                    "action": int(action),
                    "source": int(source) if source is not None else 0,
                    "slot": _as_int(slot, 0),
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
        if not self.active(CH_ACTIVITY):
            return
        data = event.data
        name = data.get("user_name")
        if not name:
            return
        serial = str(data.get("serial") or "").replace(":", "").replace("-", "").lower()
        if not self.ieee or serial != self.ieee.replace(":", "").replace("-", "").lower():
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

    async def _async_set_pin(self, slot: int, code: str) -> None:
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        if not await self.zha.set_pin(slot, code):
            raise RuntimeError("the lock did not accept the PIN command")
        self.slots.mark_pin(slot, True)
        await self._async_publish_slot(slot, self.slots.occupied(slot))

    async def _async_clear_pin(self, slot: int) -> None:
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        if not await self.zha.clear_pin(slot):
            raise RuntimeError("the lock did not accept the clear command")
        self.slots.mark_pin(slot, False)
        await self._async_publish_slot(slot, self.slots.occupied(slot))

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


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default
