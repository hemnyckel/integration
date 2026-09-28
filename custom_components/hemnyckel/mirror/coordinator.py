"""The local engine — the real lock, its slots, guests and journal.

Everything here is the local path:

- lock -> journal:  the lock's own operation events (ZhaLink, attribute 0x0100)
- HA -> lock:       PIN, fingerprint, settings, slots and guests

The app side (the emulator and its bridge) has been removed, so the real slot
number is the truth and nothing is mirrored anywhere.

Everything happens in code: the user writes no automations and no YAML.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_call_later,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from ..const import (
    ACTION_NAMES,
    CONF_DEVICE_IDENTITY,
    CONF_LOCK_ENTITY,
    CONF_LOCK_IEEE,
    DEFAULT_ENDPOINT,
    DOMAIN,
    EVENT_JOURNAL,
    HEALTH_INTERVAL,
    HUMAN_SOURCES,
    SOURCE_NAMES,
    SRC_FINGERPRINT,
    SRC_KEYPAD,
    SRC_RFID,
    ZCL_CMD_FP_CLEAR,
    ZCL_CMD_FP_ENROLL,
    ZCL_CMD_TAG_CLEAR,
)

from .facts import placeholder_slot_name
from .identity import (
    entity_id_on_serial,
    normalise_serial,
    renamed_entity_id,
    zha_ieee,
)
from .guests import (
    GUEST_CREATED,
    GUEST_EXPIRED,
    GUEST_REVOKED,
    GUEST_USED,
    GUEST_WINDOW_CLOSE,
    GUEST_WINDOW_OPEN,
    expired_slots,
    generate_code,
    is_expired,
    normalize_until,
    pick_slot,
    valid_code,
)
from .journal import (
    ORIGIN_HA,
    ORIGIN_LOCK,
    add as journal_add,
    make_entry,
    query as journal_query,
    summarize as journal_summarize,
    trim as journal_trim,
)
from .pin_rules import FIRST_USER_SLOT, check_credential_slot, pin_capacity
from .schedule import describe as schedule_describe
from .schedule import in_window, next_boundary, normalize_windows
from .slots import SlotTable
from .zha_link import FACTS_ATTRIBUTES, ZhaLink

_LOGGER = logging.getLogger(__name__)

# Seconds between opportunistic facts reads right after the lock was awake.
FACTS_MIN_INTERVAL = 300


class MirrorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds the local lock state and runs everything that needs the lock."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        lock_entity_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )
        self.entry = entry
        self.lock_entity_id = lock_entity_id

        self.last_event: dict[str, Any] | None = None
        self.slots = SlotTable(hass, entry)
        self.lock_facts: dict[str, Any] = {}
        self.lock_facts_at: str | None = None
        self._facts_refresh_monotonic: float = 0.0
        self._facts_task: asyncio.Task[Any] | None = None
        self.journal: list[dict[str, Any]] = []
        self._journal_path = hass.config.path(
            ".hemnyckel", f"journal_{entry.entry_id}.jsonl"
        )
        self._journal_lock = asyncio.Lock()
        self.guests: dict[str, dict[str, Any]] = {}
        self._guest_unsubs: dict[str, Callable[[], None]] = {}
        self.zha: ZhaLink | None = None
        self.counters: dict[str, int] = {
            "events": 0,
            "errors": 0,
        }

        # Derived entities and metadata
        self.related: dict[str, str] = {}
        self.ieee: str | None = None
        self.endpoint_id: int = DEFAULT_ENDPOINT

        self._unsubs: list[Callable[[], None]] = []
        self._started = False

    # -- lifecycle ----------------------------------------------------------

    async def async_setup(self) -> None:
        """Attach to the real lock. Idempotent."""
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
            self.hass.bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED, self._on_device_registry_updated
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_entity_registry_updated
            )
        )

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
        await self._async_resume_guests()
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
        return normalise_serial(value)

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

    @callback
    def _on_entity_registry_updated(self, event: Any) -> None:
        """Follow our lock when its entity is renamed, so the link survives."""
        data = event.data if isinstance(event.data, dict) else {}
        new_entity_id = renamed_entity_id(data, self.lock_entity_id)
        if new_entity_id is not None:
            self._apply_lock_entity_id(new_entity_id, reason="was renamed")

    # -- metadata -----------------------------------------------------------

    def _discover_lock_metadata(self) -> None:
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.lock_entity_id)
        if entry is None:
            # A rename replaces the entity id but not the registry entry, so a
            # stored id that no longer exists means the rename happened while
            # we were not listening (or the lock was re-paired). Recover by the
            # stored serial before giving up: a detached ZHA link means no
            # journal, no slots and no guest codes, and must never be quiet.
            recovered = self._resolve_lock_entity_id()
            if recovered is None:
                _LOGGER.error(
                    "The lock entity %s is not in the entity registry and no "
                    "lock with the stored Zigbee serial could be found; the "
                    "ZHA link stays detached - no journal, slots or guest "
                    "codes - until the entry points at an existing lock entity",
                    self.lock_entity_id,
                )
                return
            _LOGGER.warning(
                "The lock entity %s was renamed; following it to %s",
                self.lock_entity_id,
                recovered,
            )
            self._apply_lock_entity_id(recovered, reason="was renamed")
            entry = registry.async_get(recovered)
            if entry is None:
                _LOGGER.error(
                    "The renamed lock entity %s is still not in the registry",
                    recovered,
                )
                return

        if entry.device_id:
            dev_reg = dr.async_get(self.hass)
            device = dev_reg.async_get(entry.device_id)
            if device:
                ieee = zha_ieee(device.identifiers)
                if ieee:
                    self.ieee = ieee

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

        if not self.ieee:
            _LOGGER.error(
                "The lock entity %s has no Zigbee device, so the ZHA link "
                "stays detached - no journal, slots or guest codes",
                self.lock_entity_id,
            )
        elif self.entry.data.get(CONF_LOCK_IEEE) != self.ieee:
            # Remember the serial so a later setup can follow a rename (or a
            # re-paired module) back to this lock.
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_LOCK_IEEE: self.ieee},
            )

        _LOGGER.debug(
            "mirror metadata: ieee=%s ep=%s relaterade=%s",
            self.ieee,
            self.endpoint_id,
            self.related,
        )

    def _resolve_lock_entity_id(self) -> str | None:
        """Find the lock entity from the serial stored on the entry.

        The entity registry is keyed by the entity id, which a rename replaces;
        the ZHA device behind it keeps its serial. Used only when the stored
        ``lock_entity_id`` no longer exists.
        """
        serial = self.entry.data.get(CONF_LOCK_IEEE)
        if not serial:
            return None
        registry = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        device_values: dict[str, list[str]] = {}
        for device in dev_reg.devices:
            device_values[device.id] = [
                value for _kind, value in device.identifiers
            ] + [value for _kind, value in device.connections]
        entities = [
            {
                "entity_id": item.entity_id,
                "domain": item.domain,
                "device_id": item.device_id,
                "disabled_by": item.disabled_by,
            }
            for item in registry.entities.values()
        ]
        return entity_id_on_serial(entities, device_values, serial)

    @callback
    def _apply_lock_entity_id(self, entity_id: str, *, reason: str) -> None:
        """Point the coordinator, the ZHA link and the entry at the new id."""
        old = self.lock_entity_id
        if not entity_id or entity_id == old:
            return
        self.lock_entity_id = entity_id
        if self.zha is not None:
            self.zha.lock_entity_id = entity_id
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, CONF_LOCK_ENTITY: entity_id},
        )
        _LOGGER.info("Lock entity %s %s to %s", old, reason, entity_id)

    # -- publishing ---------------------------------------------------------

    @callback
    def _snapshot(self) -> dict[str, Any]:
        return {
            "last_event": self.last_event,
            "counters": dict(self.counters),
        }

    @callback
    def _publish_snapshot(self) -> None:
        self.async_set_updated_data(self._snapshot())

    # -- local slots --------------------------------------------------------

    def _issue_id(self, key: str) -> str:
        """A repair issue id that is unique to this entry.

        Several mirrors (one per lock) share the issue registry, so a bare
        "new_slot_5" would point at whichever lock happened to raise it first.
        """
        return f"{key}_{self.entry.entry_id[:8]}"

    def _slot_is_named(self, slot: int) -> bool:
        """A real name, not the import's placeholder (which may be replaced)."""
        return not placeholder_slot_name(self.slots.get(slot).get("name"))

    @callback
    def _flag_new_slot(self, slot: int, kind: str) -> None:
        """Raise a fixable repair when the lock used a slot we have no name for."""
        if slot < 3 or self._slot_is_named(slot):
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
        """A name for a freshly learned slot, when exactly one fits.

        The journal remembers names attached to this slot's events, which
        covers guests the slot table does not name. The suggestion is
        conservative and the user still confirms it in the repair flow.
        """
        if self._slot_is_named(slot):
            return None
        used = {
            str(data.get("name") or "").lower()
            for _slot, data in self.slots.items()
            if data.get("name")
        }
        for entry in reversed(self.journal):
            if entry.get("slot") != slot or not entry.get("name"):
                continue
            name = str(entry["name"])
            if name.lower() not in used:
                return name
            break
        return None

    async def async_set_slot_pin(self, slot: int, code: str) -> None:
        """Write a PIN to a slot on the real lock (config UI and service path)."""
        await self._async_set_pin(slot, code)

    async def async_clear_slot(self, slot: int) -> None:
        """Clear a slot's credential on the real lock and forget it locally."""
        await self._async_clear_pin(slot)
        self.slots.clear(slot)
        # The repairs that asked for a name point at a slot that no longer
        # holds anything.
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(f"new_slot_{slot}"))
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

        floor = FIRST_USER_SLOT
        slot_numbers = {slot for slot, _data in self.slots.items() if slot >= floor}
        report: dict[str, Any] = {
            "dry_run": dry_run,
            "master_floor": floor,
            "slots": [],
            "guests": [],
            "tags": [],
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
        self._publish_snapshot()
        return self.lock_facts

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
        await self._async_journal_add(
            make_entry(
                action="setting_changed",
                time=dt_util.utcnow().isoformat(),
                origin=ORIGIN_HA,
                detail=f"sound_volume={int(level)}",
            )
        )
        return facts

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
            # The config entry id changes if the entry is ever re-created,
            # so the event also carries the lock entity: a stable name for
            # the same door, which is what a consumer should key on.
            self.hass.bus.async_fire(
                EVENT_JOURNAL,
                dict(stored)
                | {"entry_id": self.entry.entry_id, "lock": self.lock_entity_id},
            )
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

    async def async_apply_guest_code(self, slot: int, code: str) -> None:
        """Write a new value for a guest's code into the lock and the catalog.

        The value goes into the lock first so the catalog and the lock cannot
        drift; a later restore replays the new value.
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
            occupied |= {int(key) for key in self.guests if str(key).isdigit()}
            slot = pick_slot(
                occupied,
                FIRST_USER_SLOT,
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
        until: str | None = None,
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
        # A recurring guest may also carry an end date: the same helper the
        # simple guest uses expires it, so a cleaner's code does not outlive
        # the arrangement.
        normalized_until = normalize_until(until) if until else None
        if until and normalized_until is None:
            raise RuntimeError("until is not a valid ISO timestamp")
        if slot is None:
            occupied = {s for s, _data in self.slots.items() if self.slots.occupied(s)}
            occupied |= {int(key) for key in self.guests if str(key).isdigit()}
            slot = pick_slot(
                occupied,
                FIRST_USER_SLOT,
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
            **({"until": normalized_until} if normalized_until else {}),
            **({"group": str(group)} if group else {}),
        }
        self.slots.set_name(slot, clean_name)
        await self._async_save_guests()
        await self._apply_guest_state(slot)
        self._schedule_guest_boundary(slot)
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
            row: dict[str, Any] = {
                "slot": int(key) if key.isdigit() else None,
                "name": guest.get("name"),
                "kind": kind,
                "created": guest.get("created"),
                "has_code": key.isdigit() and "pin" in self.slots.credentials(int(key)),
                "group": guest.get("group"),
                # True when the catalog holds the PIN value itself, so the code
                # can be replayed after a loss; a temporary guest's code is
                # shown once and never stored.
                "restorable": bool(guest.get("code")),
            }
            if kind == "recurring":
                row["schedule"] = windows
                row["summary"] = schedule_describe(windows)
                row["paused"] = bool(guest.get("paused"))
                row["until"] = guest.get("until")
                row["in_window"] = bool(
                    windows and not guest.get("paused") and in_window(windows, now)
                )
                if guest.get("paused"):
                    row["state"] = "paused"
                elif is_expired(guest.get("until"), now):
                    # Past its end date the window no longer matters.
                    row["state"] = "expired"
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

    # -- state ------------------------------------------------------------------

    @callback
    # -- lock -> journal --------------------------------------------------------

    @callback
    def _on_lock_activity(self, data: dict[str, Any]) -> None:
        """A decoded operation event from the lock: learn it and journal it."""
        action = data.get("action_code")
        source = data.get("source_code")
        if not isinstance(action, int):
            return
        slot = data.get("user_slot")
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
            self._flag_new_slot(slot, label)
            guest = self.guests.get(str(slot))
            if isinstance(guest, dict) and guest.get("one_time"):
                # A one-time code has done its job: revoke it after this use.
                self.hass.async_create_task(
                    self.async_revoke_guest(slot, reason=GUEST_USED)
                )
        # System locks (auto) need no notification, but are journaled anyway.
        if source is not None and source not in HUMAN_SOURCES:
            _LOGGER.debug("Skipping system event source=%s", source)
        master = bool(data.get("master"))
        if master:
            slot_name = self.slots.name(0, fallback=False) or "Master"
        elif isinstance(slot, int):
            slot_name = self.slots.name(slot)
        else:
            slot_name = None
        self.last_event = {
            "action": ACTION_NAMES.get(action),
            "source": SOURCE_NAMES.get(source) if isinstance(source, int) else None,
            "slot": slot,
            "name": slot_name,
            "time": dt_util.utcnow().isoformat(),
            "direction": "lock",
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
                    slot=slot,
                    name=slot_name,
                )
            )
        )

    # -- periodic update --------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Periodic refresh: publish the current snapshot.

        The lock reports its own changes, so there is no polling of the device;
        the tick only keeps the coordinator's data fresh.
        """
        return self._snapshot()

    # -- PIN / fingerprint towards ZHA ---------------------------------------

    async def _async_set_pin(
        self,
        slot: int,
        code: str,
        *,
        journal: dict[str, Any] | None = None,
    ) -> None:
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        reason = check_credential_slot(slot, self.lock_facts)
        if reason is not None:
            raise RuntimeError(reason)
        if not await self.zha.set_pin(slot, code):
            raise RuntimeError("the lock did not accept the PIN command")
        self.slots.mark_pin(slot, True)
        entry = make_entry(
            time=dt_util.utcnow().isoformat(),
            origin=ORIGIN_HA,
            slot=slot,
            **(
                journal
                or {
                    "action": "pin_set",
                    "name": self.slots.name(slot, fallback=False) or None,
                }
            ),
        )
        await self._async_journal_add(entry)

    async def _async_clear_pin(
        self,
        slot: int,
        *,
        journal: dict[str, Any] | None = None,
    ) -> None:
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        reason = check_credential_slot(slot, self.lock_facts)
        if reason is not None:
            raise RuntimeError(reason)
        if not await self.zha.clear_pin(slot):
            raise RuntimeError("the lock did not accept the clear command")
        self.slots.mark_pin(slot, False)
        entry = make_entry(
            time=dt_util.utcnow().isoformat(),
            origin=ORIGIN_HA,
            slot=slot,
            **(journal or {"action": "pin_cleared"}),
        )
        await self._async_journal_add(entry)

    async def async_start_finger_enroll(self, slot: int, mode: str = "auto") -> dict[str, Any]:
        """Start a fingerprint enrollment for one slot.

        The lock reports nothing while an enrollment runs — a template exists
        only once someone has really opened the door with that finger — so this
        lights the reader and leaves the touch to the person at the door.
        ``mode`` is ``auto`` or ``local``; both enroll locally.
        """
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        reason = check_credential_slot(slot, self.lock_facts)
        if reason is not None:
            raise RuntimeError(reason)
        guest = self.guests.get(str(slot))
        name = (guest or {}).get("name") or self.slots.name(slot, fallback=False)

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
        self._publish_snapshot()
        return {"slot": slot, "name": name, "via": "local"}

    async def _async_zcl(self, command: int, arg: int) -> None:
        if self.zha is None:
            raise RuntimeError("the ZHA link is not ready")
        if not await self.zha.send_fingerprint(command, arg):
            raise RuntimeError(f"the lock did not accept command 0x{command:02x}")

    # -- health -------------------------------------------------------------

    async def _async_health(self, _now: Any = None) -> None:
        if self.zha is not None:
            self.zha.ensure_listener()
            self.zha.maybe_configure_reporting()
