"""The ZHA link: device lookup, the raw operation-event listener and ZCL commands.

The mirror owns this link so the product no longer depends on another integration's
local patch. The listener decodes attribute 0x0100 straight off the zigpy cluster,
because the module sends it profile-wide while the ZHA quirk only matches the
manufacturer-specific form. Attachment is verified on the health tick, so a ZHA
device rebuild cannot silently kill attribution.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from collections.abc import Callable

from homeassistant.core import HomeAssistant, callback

from ..const import ZCL_CLUSTER_DOORLOCK, ZHA_SERVICE, decode_operation_event

_LOGGER = logging.getLogger(__name__)

ATTR_OPERATION_EVENT = 0x0100
ZHA_DOMAIN = "zha"

# Standard DoorLock attributes worth reading for diagnostics, by name so a
# zigpy version difference cannot break the read. Credential material is never
# read: attribute 0x0101 stays write-only (repository rules).
DEFAULT_READ_NAMES: list[int | str] = [
    "lock_state",
    "actuator_enabled",
    "door_state",
    "num_of_total_users_supported",
    "num_of_pin_users_supported",
    "num_of_rfid_users_supported",
    "max_pin_len",
    "min_pin_len",
    "max_rfid_len",
    "min_rfid_len",
    "auto_relock_time",
    "sound_volume",
    "operating_mode",
    "supported_operating_modes",
]

# Attribute ids that must never be read (PIN codes).
NEVER_READ_IDS: set[int] = {0x0101}

# The lock's own capabilities and settings, refreshed opportunistically while
# the lock is awake and on demand through the services.
FACTS_ATTRIBUTES: list[int | str] = [
    "lock_state",
    "door_state",
    "num_of_total_users_supported",
    "num_of_pin_users_supported",
    "num_of_rfid_users_supported",
    "auto_relock_time",
    "sound_volume",
]


def _json_safe(data: Any, cluster: Any = None) -> dict[str, Any]:
    """Normalize a zigpy result dict into JSON-safe, labeled values."""
    result: dict[str, Any] = {}
    try:
        items = data.items()
    except AttributeError:
        return result
    for key, value in items:
        if isinstance(value, (bytes, bytearray)):
            value = value.hex()
        elif hasattr(value, "value"):
            value = value.value
        label: Any = key
        try:
            attr_id = int(key)
            definition = (cluster.attributes if cluster is not None else {}).get(
                attr_id
            )
            label = getattr(definition, "name", None) or f"0x{attr_id:04x}"
        except (TypeError, ValueError):
            label = key
        if not isinstance(value, (int, float, str, bool, type(None))):
            value = str(value)
        result[str(label)] = value
    return result


class ZhaLink:
    """One lock's Zigbee access: cluster lookup, raw reports and commands."""

    def __init__(
        self,
        hass: HomeAssistant,
        ieee: str,
        lock_entity_id: str,
        endpoint_id: int,
        on_activity: Callable[[dict[str, Any]], None],
    ) -> None:
        self.hass = hass
        self.ieee = ieee
        self.lock_entity_id = lock_entity_id
        self.endpoint_id = endpoint_id
        self._on_activity = on_activity
        self._unsub: Callable[[], None] | None = None
        self._cluster: Any = None

    # -- cluster lookup -----------------------------------------------------

    def _find_cluster(self) -> Any:
        """The DoorLock cluster for this lock, walking the ZHA object chain.

        ZHA wraps the zigpy device in several layers (ZHADeviceProxy to Device
        to CustomDeviceV2); the clusters live on the deepest object.
        """
        data = self.hass.data.get(ZHA_DOMAIN)
        if data is None:
            return None
        proxies = getattr(getattr(data, "gateway_proxy", None), "device_proxies", None)
        if not proxies:
            return None
        for dev_ieee, device_proxy in proxies.items():
            if str(dev_ieee).lower() != str(self.ieee).lower():
                continue
            obj = device_proxy
            for _ in range(4):
                endpoints = getattr(obj, "endpoints", None)
                if endpoints:
                    for ep_id, endpoint in endpoints.items():
                        if ep_id == 0:
                            continue
                        cluster = getattr(endpoint, "in_clusters", {}).get(
                            ZCL_CLUSTER_DOORLOCK
                        )
                        if cluster is not None:
                            return cluster
                obj = getattr(obj, "device", None)
                if obj is None:
                    break
        return None

    # -- raw 0x0100 listener -------------------------------------------------

    @property
    def attached(self) -> bool:
        return self._unsub is not None

    def attach_listener(self) -> bool:
        """Attach the raw listener to the current cluster object."""
        if self.attached:
            return True
        cluster = self._find_cluster()
        if cluster is None:
            return False
        self._cluster = cluster
        self._unsub = cluster.on_event("attribute_report", self._handle_report)
        _LOGGER.info("Raw 0x0100 listener attached for %s", self.ieee)
        return True

    def ensure_listener(self) -> None:
        """Attach, or re-attach after a ZHA device rebuild."""
        if self._unsub is not None and self._find_cluster() is self._cluster:
            return
        self.detach()
        self.attach_listener()

    def detach(self) -> None:
        if self._unsub is not None:
            try:
                self._unsub()
            except Exception:  # noqa: BLE001 - never crash a teardown
                _LOGGER.debug("Listener detach failed", exc_info=True)
        self._unsub = None
        self._cluster = None

    @callback
    def _handle_report(self, event: Any) -> None:
        if getattr(event, "attribute_id", None) != ATTR_OPERATION_EVENT:
            return
        raw = getattr(event, "raw_value", None)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            try:
                value = int(raw.value)
            except (TypeError, ValueError, AttributeError):
                _LOGGER.warning("Could not parse operation event: %s", raw)
                return
        decoded = decode_operation_event(value)
        if decoded is not None:
            self._on_activity(decoded)

    # -- commands ------------------------------------------------------------

    async def send_command(
        self,
        command: int,
        *,
        params: dict[str, Any] | None = None,
        args: list[Any] | None = None,
    ) -> bool:
        """Send a ZCL command over ZHA with the Nimly wake-and-retry pattern.

        An IndexError while parsing the response means the command reached the
        lock in a shape zigpy cannot parse; that still counts as sent.
        """
        for attempt in range(2):
            data: dict[str, Any] = {
                "ieee": self.ieee,
                "endpoint_id": self.endpoint_id,
                "cluster_id": ZCL_CLUSTER_DOORLOCK,
                "cluster_type": "in",
                "command": command,
                "command_type": "server",
            }
            if params is not None:
                data["params"] = params
            if args is not None:
                data["args"] = args
            try:
                await self.hass.services.async_call(
                    "zha", ZHA_SERVICE, data, blocking=True
                )
                return True
            except IndexError:
                # The lock answers SetPINCode in a shape zigpy cannot parse; the
                # command itself was sent and received.
                _LOGGER.debug(
                    "Nimly response quirk (IndexError) for 0x%02x - sent anyway", command
                )
                return True
            except TimeoutError:
                if attempt == 0:
                    _LOGGER.debug("Timeout for 0x%02x - waking the lock", command)
                    await self._wake_lock()
                    continue
                _LOGGER.warning(
                    "Timeout for 0x%02x to %s after wake and retry", command, self.ieee
                )
                return False
            except Exception:  # noqa: BLE001 - reported to the caller as failure
                _LOGGER.exception("Failed to send 0x%02x to %s", command, self.ieee)
                return False
        return False

    async def set_pin(self, slot: int, code: str) -> bool:
        """Set a PIN code in a slot (SetPINCode, ZCL 0x05)."""
        return await self.send_command(
            0x0005,
            params={
                "user_id": slot,
                "user_status": 1,
                "user_type": 0,
                "pin_code": code,
            },
        )

    async def clear_pin(self, slot: int) -> bool:
        """Clear a slot's credential (ClearPINCode, ZCL 0x07)."""
        return await self.send_command(0x0007, params={"user_id": slot})

    async def send_vendor_detailed(self, command: int, arg: int) -> dict[str, Any]:
        """Send a raw vendor command and return the lock's reply, verbatim.

        For commands the vendor app knows and we are still learning (the tag
        scan, 0x70): the reply is data to inspect whole, not a boolean.
        """
        cluster = self._find_cluster()
        if cluster is None:
            return {"ok": False, "error": "the cluster was not found"}
        try:
            from zigpy import types as t

            async with asyncio.timeout(30):
                reply = await cluster.request(
                    False, command, t.uint16_t, arg, expect_reply=True
                )
        except TimeoutError:
            return {"ok": False, "error": "timeout"}
        except Exception as err:  # noqa: BLE001
            return {"ok": False, "error": str(err)[:120]}
        try:
            payload = reply[1] if isinstance(reply, (tuple, list)) else reply
            return {"ok": True, "reply": bytes(payload).hex(" ")}
        except Exception:  # noqa: BLE001
            return {"ok": True, "reply": repr(reply)[:160]}

    async def send_fingerprint(self, command: int, slot: int) -> bool:
        """The raw fingerprint commands 0x71/0x72, sent as a uint16 argument.

        Tried on the cluster object first (the proven path), then through the
        ZHA service.
        """
        cluster = self._find_cluster()
        if cluster is not None:
            try:
                from zigpy import types as t

                async with asyncio.timeout(30):
                    await cluster.request(
                        False, command, t.uint16_t, slot, expect_reply=True
                    )
                return True
            except TimeoutError:
                _LOGGER.warning(
                    "Timeout for fingerprint command 0x%02x to %s", command, self.ieee
                )
                return False
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Cluster request for 0x%02x failed, trying the service", command
                )
        return await self.send_command(command, args=[slot])

    # -- attribute reads ------------------------------------------------------

    async def read_attributes(
        self, attributes: list[int | str] | None = None, *, wake: bool = True
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read standard DoorLock attributes, waking the lock once on timeout.

        Names or ids are accepted; attributes unknown to this zigpy build and
        credential material (0x0101) are skipped and reported, never read.
        ``wake=False`` never actuates the lock - used for background refreshes
        right after the lock was awake anyway.
        Returns (success, failure) with JSON-safe, labeled values.
        """
        cluster = self._find_cluster()
        if cluster is None:
            return {}, {"error": "the DoorLock cluster was not found"}
        wanted: list[int | str] = attributes if attributes is not None else list(
            DEFAULT_READ_NAMES
        )
        resolved: list[int] = []
        skipped: list[str] = []
        for item in wanted:
            try:
                attr_id = int(item)
                if attr_id in NEVER_READ_IDS:
                    skipped.append(f"{item} (credential attribute) ")
                    continue
                cluster.find_attribute(attr_id)
            except (TypeError, ValueError):
                try:
                    attr_id = cluster.find_attribute(str(item)).id
                except Exception:  # noqa: BLE001 - unknown to this zigpy build
                    skipped.append(str(item))
                    continue
            except Exception:  # noqa: BLE001 - unknown to this zigpy build
                skipped.append(str(item))
                continue
            resolved.append(attr_id)
        if not resolved:
            return {}, {"error": "no known attributes", "skipped": skipped}
        for attempt in range(2):
            try:
                async with asyncio.timeout(45):
                    success, failure = await cluster.read_attributes(
                        resolved, allow_cache=False
                    )
                ok = _json_safe(success, cluster)
                failed = _json_safe(failure, cluster)
                if skipped:
                    failed["skipped"] = skipped
                return ok, failed
            except TimeoutError:
                if attempt == 0 and wake:
                    _LOGGER.debug("Attribute read timed out - waking the lock")
                    await self._wake_lock()
                    continue
                return {}, {"error": "timeout"}
            except Exception as err:  # noqa: BLE001 - reported to the caller
                _LOGGER.debug("Attribute read failed: %s", err, exc_info=True)
                return {}, {"error": str(err)}
        return {}, {"error": "unreachable"}

    async def write_attributes(
        self, attributes: dict[str | int, Any], *, wake: bool = True
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Write ZCL attributes, waking the lock once on timeout if allowed.

        The caller confirms the result by reading the attribute back: some
        modules acknowledge a write without echoing a value.
        """
        cluster = self._find_cluster()
        if cluster is None:
            return {}, {"error": "the DoorLock cluster was not found"}
        for attempt in range(2):
            try:
                async with asyncio.timeout(45):
                    success, failure = await cluster.write_attributes(attributes)
                return _json_safe(success, cluster), _json_safe(failure, cluster)
            except TimeoutError:
                if attempt == 0 and wake:
                    _LOGGER.debug("Attribute write timed out - waking the lock")
                    await self._wake_lock()
                    continue
                return {}, {"error": "timeout"}
            except Exception as err:  # noqa: BLE001 - reported to the caller
                _LOGGER.debug("Attribute write failed: %s", err, exc_info=True)
                return {}, {"error": str(err)}
        return {}, {"error": "unreachable"}

    async def _wake_lock(self) -> None:
        """Wake the radio by locking through the ZHA lock entity.

        This is a physical actuation (an open door drives the bolt into the
        air), kept because ZHA's lock entity wraps sleepy end devices in
        extended timeouts and retries, which reliably re-arms the parent
        router's short message window; plain attribute reads just time out.
        """
        try:
            await self.hass.services.async_call(
                "lock", "lock", {"entity_id": self.lock_entity_id}, blocking=True
            )
            await asyncio.sleep(1)
        except Exception:  # noqa: BLE001 - best effort
            _LOGGER.debug("Wake attempt failed, proceeding anyway")
