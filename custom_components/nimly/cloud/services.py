"""Services for the cloud layer.

The write services are deliberately explicit rather than entities: the local mirror layer
is the primary way to control a lock, and the cloud is an optional second one.
Making them services keeps that choice visible in the automation.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from ..const import (
    DOMAIN,
    SERVICE_CLEANUP_CLOUD,
    SERVICE_AUDIT,
    SERVICE_RESTORE_CLOUD,
    SERVICE_CLOUD_GUESTS,
    SERVICE_SYNC_CLOUD,
    SERVICE_FETCH_HISTORY,
    SERVICE_GATEWAY_SCAN,
    SERVICE_PROBE,
    SERVICE_REFRESH,
    SERVICE_SET_LOCK,
    SERVICE_UPDATE_CLOUD_GUEST,
    SERVICE_DELETE_CLOUD_GUEST,
    SERVICE_SET_CLOUD_CODE,
    SERVICE_LINK_CLOUD_GUEST,
    SERVICE_REPAIR_JOIN,
)
from .api import NimlyCloudError
from .coordinator import NimlyCloudCoordinator
from ..mirror.coordinator import MirrorCoordinator

_LOGGER = logging.getLogger(__name__)

PROBE_MAX = 25

# How long to wait before re-reading the lock after a timed-out acknowledgement.
CONFIRM_DELAY = 8

SCHEMA_REFRESH = vol.Schema({vol.Optional("entry_id"): cv.string})

SCHEMA_FETCH_HISTORY = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=250)),
    }
)

SCHEMA_SET_LOCK = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("locked"): cv.boolean,
    }
)

SCHEMA_GATEWAY_SCAN = vol.Schema({vol.Required("start"): cv.boolean})

SCHEMA_PROBE = vol.Schema({vol.Required("paths"): vol.All(cv.ensure_list, [vol.Any(str, dict)])})

SCHEMA_CLEANUP_CLOUD = vol.Schema({vol.Optional("dry_run", default=False): cv.boolean})

SCHEMA_CLOUD_GUESTS = vol.Schema({vol.Optional("entry_id"): cv.string})

SCHEMA_PROBE = vol.Schema({vol.Required("paths"): vol.All(cv.ensure_list, [vol.Any(str, dict)])})

SCHEMA_CLEANUP_CLOUD = vol.Schema({vol.Optional("dry_run", default=False): cv.boolean})

SCHEMA_CLOUD_GUESTS = vol.Schema({vol.Optional("entry_id"): cv.string})

SCHEMA_AUDIT = vol.Schema({vol.Optional("entry_id"): cv.string})

SCHEMA_RESTORE_CLOUD = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("dry_run", default=True): cv.boolean,
    }
)

SCHEMA_SYNC_CLOUD = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("dry_run", default=True): cv.boolean,
    }
)

SCHEMA_UPDATE_CLOUD_GUEST = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("user_id"): cv.string,
        vol.Optional("guest"): cv.string,
        vol.Optional("new_name"): cv.string,
        vol.Optional("valid_from"): cv.string,
        vol.Optional("valid_to"): cv.string,
        vol.Optional("email"): cv.string,
        vol.Optional("phone"): cv.string,
    }
)

SCHEMA_DELETE_CLOUD_GUEST = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("user_id"): cv.string,
        vol.Optional("guest"): cv.string,
    }
)

SCHEMA_REPAIR_JOIN = vol.Schema({vol.Optional("entry_id"): cv.string})

SCHEMA_LINK_CLOUD_GUEST = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("slot"): vol.Coerce(int),
        vol.Optional("user_id"): cv.string,
        vol.Optional("guest"): cv.string,
    }
)

SCHEMA_SET_CLOUD_CODE = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("user_id"): cv.string,
        vol.Optional("guest"): cv.string,
        vol.Required("type", default="pin"): vol.In(["pin", "tag"]),
        vol.Required("value"): vol.All(cv.string, vol.Length(min=4, max=32)),
    }
)


def _mirrors(hass: HomeAssistant) -> list[Any]:
    from ..mirror.coordinator import MirrorCoordinator

    return [
        item
        for item in hass.data.get(DOMAIN, {}).values()
        if isinstance(item, MirrorCoordinator)
    ]


def _find_guest(
    guests: list[dict[str, Any]],
    user_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Resolve one guest by id, or by a name that must be unambiguous."""
    if user_id:
        for guest in guests:
            if str(guest.get("id")) == str(user_id):
                return guest
        raise HomeAssistantError(f"no guest with id {user_id}")
    if name:
        wanted = name.strip().casefold()
        exact = [
            guest
            for guest in guests
            if str(guest.get("name") or "").strip().casefold() == wanted
        ]
        hits = exact or [
            guest
            for guest in guests
            if wanted in str(guest.get("name") or "").strip().casefold()
        ]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise HomeAssistantError(f"no guest matches {name!r}")
        raise HomeAssistantError(
            f"{len(hits)} guests match {name!r}; pass user_id to pick one"
        )
    raise HomeAssistantError("pass user_id (preferred) or guest")


def _lock_device(
    hass: HomeAssistant, coordinator: NimlyCloudCoordinator, entry_id: str | None
) -> tuple[Any, str]:
    """The mirror and vendor device a lock-scoped service call refers to.

    With one lock the entry can be left out; with several the caller must say
    which lock, so a code is never written to the wrong door.
    """
    from .sync import cloud_device_for

    mirrors = _mirrors(hass)
    if entry_id:
        chosen = [m for m in mirrors if m.entry.entry_id == entry_id]
        if not chosen:
            raise HomeAssistantError(f"no lock with entry_id {entry_id}")
        mirror = chosen[0]
        device_id = cloud_device_for(coordinator, mirror)
        if device_id is None:
            raise HomeAssistantError(
                f"lock {mirror.name!r} has no matching device in the cloud account"
            )
        return mirror, device_id
    placed = [
        (mirror, cloud_device_for(coordinator, mirror)) for mirror in mirrors
    ]
    placed = [(mirror, device_id) for mirror, device_id in placed if device_id]
    if len(placed) == 1:
        return placed[0]
    if not placed:
        raise HomeAssistantError("no lock is synced to the cloud account")
    names = ", ".join(repr(mirror.name) for mirror, _ in placed)
    raise HomeAssistantError(
        f"several locks are synced ({names}); pass entry_id to choose one"
    )


def _coordinator(hass: HomeAssistant, entry_id: str | None = None) -> NimlyCloudCoordinator:
    entries = {
        key: coordinator
        for key, coordinator in hass.data.get(DOMAIN, {}).items()
        if isinstance(coordinator, NimlyCloudCoordinator)
    }
    if not entries:
        raise HomeAssistantError("the Nimly cloud account is not configured")
    if not entry_id:
        return next(iter(entries.values()))
    if entry_id in entries:
        return entries[entry_id]
    # A lock's entry id is accepted wherever the account is implied: services
    # that also take a lock (the card passes the sensor's entry id) must not
    # silently act on the wrong account when an id is given but unknown.
    mirrors = {
        key: coordinator
        for key, coordinator in hass.data.get(DOMAIN, {}).items()
        if isinstance(coordinator, MirrorCoordinator)
    }
    if entry_id in mirrors:
        if len(entries) > 1:
            raise HomeAssistantError(
                "several cloud accounts are configured; pass the account entry id"
            )
        return next(iter(entries.values()))
    raise HomeAssistantError(f"no account or lock with entry id {entry_id}")


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register every service once, at integration setup."""

    async def _refresh(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call.data.get("entry_id"))
        await coordinator.async_request_refresh()
        return {"ok": True}

    async def _fetch_history(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        limit: int = call.data["limit"]
        device_id = call.data.get("device_id")
        events = [
            event
            for event in coordinator.events
            if device_id is None or event.get("device_id") == device_id
        ]
        return {"events": events[:limit]}

    async def _set_lock(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        device_id: str = call.data["device_id"]
        locked: bool = call.data["locked"]
        try:
            response = await coordinator.api.async_set_lock(device_id, locked)
        except NimlyCloudError as err:
            if "timeout" not in str(err).lower():
                raise HomeAssistantError(f"the cloud rejected the request: {err}") from err
            # The vendor's acknowledgement can time out while the command still reaches
            # the lock. Wait, re-read the state, and report what actually happened
            # instead of failing an automation on a missing receipt.
            await asyncio.sleep(CONFIRM_DELAY)
            await coordinator.async_request_refresh()
            actual = coordinator.device_state(device_id, "lock", "state")
            if actual is not None and bool(actual) == locked:
                return {"ok": True, "confirmed": False, "message": str(err)}
            raise HomeAssistantError(
                f"the cloud accepted the request but the lock did not confirm it: {err}"
            ) from err
        await coordinator.async_request_refresh()
        return {"ok": True, "confirmed": True, "response": response}

    async def _gateway_scan(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        gateway_id = coordinator.gateway().get("id")
        if not gateway_id:
            raise HomeAssistantError("the account has no gateway")
        try:
            response = await coordinator.api.async_gateway_scan(
                str(gateway_id),
                bool(call.data["start"]),
                coordinator.location_id,
            )
        except NimlyCloudError as err:
            raise HomeAssistantError(f"the cloud rejected the request: {err}") from err
        return {"ok": True, "response": response}

    async def _probe(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        results = []
        for spec in call.data["paths"][:PROBE_MAX]:
            if isinstance(spec, str) and spec.startswith("/"):
                results.append(await coordinator.api.async_probe(spec))
            elif (
                isinstance(spec, dict)
                and isinstance(spec.get("path"), str)
                and spec["path"].startswith("/")
            ):
                results.append(
                    await coordinator.api.async_probe(
                        spec["path"],
                        spec.get("method", "GET"),
                        spec.get("json"),
                        spec.get("headers"),
                    )
                )
        return {"results": results}

    async def _link_cloud_guest(call: ServiceCall) -> dict[str, Any]:
        """Record which vendor identity one of our slots belongs to.

        The human answer to an adoption conflict ("two people, one name"): the
        link is written to the catalog, and a following sync_cloud brings the
        access in line — the same composition as every other sync decision.
        """
        cloud = _coordinator(hass)
        entry_id: str = call.data["entry_id"]
        slot: int = call.data["slot"]
        chosen = [m for m in _mirrors(hass) if m.entry.entry_id == entry_id]
        if not chosen:
            raise HomeAssistantError(f"no lock with entry_id {entry_id}")
        mirror = chosen[0]
        if str(slot) not in {key for key in mirror.guests}:
            raise HomeAssistantError(f"lock has no guest in slot {slot}")
        guests = await cloud.api.async_guest_users(cloud.location_id)
        guest = _find_guest(guests, call.data.get("user_id"), call.data.get("guest"))
        user_id = str(guest["id"])
        existing = mirror.cloud_user(slot)
        await mirror.async_set_cloud_user(slot, user_id)
        await mirror.async_journal_note(
            "guest_linked",
            detail=f"slot {slot} -> {guest.get('name') or user_id}",
        )
        return {
            "slot": slot,
            "user_id": user_id,
            "name": guest.get("name"),
            "previous": existing,
        }

    async def _cloud_guests(call: ServiceCall) -> dict[str, Any]:
        from .guests import guest_row
        from .sync import cloud_device_for

        entry_id = call.data.get("entry_id")
        coordinator = _coordinator(hass, entry_id)
        guests = await coordinator.api.async_guest_users(coordinator.location_id)
        rows = [guest_row(guest) for guest in guests]

        # With a lock selected, tell each guest apart by where its credentials
        # actually live: on this lock, on another live lock, or nowhere (the
        # vendor keeps the record but the device it named is gone).
        if entry_id:
            chosen = [m for m in _mirrors(hass) if m.entry.entry_id == entry_id]
            device_id = cloud_device_for(coordinator, chosen[0]) if chosen else None
            live_ids = {
                str(device.get("id"))
                for device in (coordinator.home.get("devices") or [])
                if device.get("id")
            }
            live_types: dict[str, set[str]] = {}
            here_types: dict[str, set[str]] = {}
            for device, accesses in coordinator.access.items():
                if device not in live_ids:
                    continue
                for access in accesses:
                    user, kind = str(access.get("userId")), str(access.get("type"))
                    if not user or not kind:
                        continue
                    live_types.setdefault(user, set()).add(kind)
                    if device == device_id:
                        here_types.setdefault(user, set()).add(kind)
            for row in rows:
                user = str(row.get("id"))
                claimed = {
                    kind
                    for kind, flag in (
                        ("pin", row["has_pin"]),
                        ("tag", row["has_tag"]),
                        ("finger", row["has_fingerprint"]),
                    )
                    if flag
                }
                here = here_types.get(user, set())
                live = live_types.get(user, set())
                row["on_lock"] = sorted(here)
                row["elsewhere"] = sorted(live - here)
                row["ghost"] = sorted(claimed - live)
        return {"guests": rows}

    async def _repair_join(call: ServiceCall) -> dict[str, Any]:
        """Bring a lock back onto the vendor bridge, the clean way.

        What used to be the ugly dance — remove the lock in the app, factory
        reset the module, start a search — in one call: drop the module's
        account record, reset the emulator's Zigbee state, open the bridge's
        join window and wait for the emulator to walk in. The bridge must be
        online; an offline bridge needs mains before anything can help.
        """
        from .identity import rename_target
        from .maintenance import device_name
        from .sync import async_sync_lock, cloud_device_for

        cloud = _coordinator(hass)
        mirrors = _mirrors(hass)
        entry_id = call.data.get("entry_id")
        if entry_id:
            chosen = [m for m in mirrors if m.entry.entry_id == entry_id]
            if not chosen:
                raise HomeAssistantError(f"no lock with entry_id {entry_id}")
            mirror = chosen[0]
        elif len(mirrors) == 1:
            mirror = mirrors[0]
        else:
            raise HomeAssistantError(
                "several locks are configured; pass entry_id to choose one"
            )

        gateway = cloud.gateway()
        gateway_id = str(gateway.get("id") or "")
        if not gateway_id:
            raise HomeAssistantError("the account has no bridge")
        if not gateway.get("online"):
            raise HomeAssistantError(
                "the bridge is offline in the cloud — power-cycle it and try again"
            )

        steps: list[str] = []

        # 1. The account refuses to (re)pair a serial it still holds.
        device_id = cloud_device_for(cloud, mirror)
        if device_id:
            try:
                await cloud.api.async_delete_device(device_id)
            except NimlyCloudError as err:
                raise HomeAssistantError(
                    f"could not remove the old device record: {err}"
                ) from err
            steps.append("device record removed")
            await cloud.async_request_refresh()

        # 2. A module holding the old network cannot do a fresh join.
        await mirror.async_forget_zigbee_network()
        steps.append("emulator reset to a fresh pairing state")
        await asyncio.sleep(12)

        # 3. The bridge only accepts joins inside its window.
        try:
            await cloud.api.async_gateway_scan(gateway_id, True)
        except NimlyCloudError as err:
            raise HomeAssistantError(f"the bridge refused the scan: {err}") from err
        steps.append("join window open")

        joined = False
        for _ in range(15):
            await asyncio.sleep(8)
            await cloud.async_request_refresh()
            if mirror.emulator_joined:
                joined = True
                break

        try:
            await cloud.api.async_gateway_scan(gateway_id, False)
        except NimlyCloudError:
            pass
        steps.append("join window closed")

        if joined:
            # The fresh record comes back with the vendor's default name and no
            # guest accesses; put the remembered name back and replay the
            # catalog so a re-pair is a replay, never a rebuild. The vendor
            # creates the record a moment after the join, so give it a window.
            new_device_id = None
            for _ in range(10):
                await cloud.async_request_refresh()
                new_device_id = cloud_device_for(cloud, mirror)
                if new_device_id:
                    break
                await asyncio.sleep(8)
            if new_device_id:
                record = next(
                    (
                        item
                        for item in cloud.devices
                        if str(item.get("id")) == new_device_id
                    ),
                    None,
                )
                wanted = rename_target(record, device_name(cloud, new_device_id))
                if wanted:
                    await cloud.api.async_rename_device(new_device_id, wanted)
                    steps.append(f"named {wanted}")
                    _LOGGER.info("Re-paired lock named %s", wanted)
            actions = await async_sync_lock(cloud, mirror, dry_run=False)
            identities = sum(
                1
                for item in actions
                if item["action"] in ("create_guest", "adopt_guest")
            )
            accesses = sum(
                1 for item in actions if item["action"] == "create_access"
            )
            steps.append(f"catalog replayed ({identities} identities, {accesses} accesses)")
            if identities or accesses:
                await mirror.async_journal_note(
                    "cloud_synced",
                    detail=f"{identities} identit(ies), {accesses} access(es)",
                )
            await cloud.async_request_refresh()

        await mirror.async_journal_note(
            "repair_join",
            detail="joined" if joined else "no join this round",
        )
        return {"joined": joined, "steps": steps, "lock": mirror.entry.entry_id}

    async def _update_cloud_guest(call: ServiceCall) -> dict[str, Any]:
        from .guests import guest_row

        coordinator = _coordinator(hass, call.data.get("entry_id"))
        guests = await coordinator.api.async_guest_users(coordinator.location_id)
        guest = _find_guest(guests, call.data.get("user_id"), call.data.get("guest"))
        fields: dict[str, Any] = {}
        for key, api_key in (("new_name", "name"), ("email", "email"), ("phone", "phone")):
            value = call.data.get(key)
            if value is not None and str(value).strip():
                fields[api_key] = str(value).strip()
        if "valid_from" in call.data or "valid_to" in call.data:
            # The vendor validates validTo against validFrom, so a window change
            # must carry both dates; keep the untouched one as it already is.
            valid_from = str(
                call.data.get("valid_from") or guest.get("validFrom") or ""
            ).strip()
            valid_to = str(
                call.data.get("valid_to") or guest.get("validTo") or ""
            ).strip()
            if not valid_from or not valid_to:
                raise HomeAssistantError(
                    "a validity change needs both valid_from and valid_to"
                )
            if len(valid_to) == 10:
                # A date-only end means the end of that day.
                valid_to = f"{valid_to}T23:59:59.000Z"
            fields["validFrom"] = valid_from
            fields["validTo"] = valid_to
        if not fields:
            raise HomeAssistantError(
                "nothing to change: pass new_name, valid_from, valid_to, email or phone"
            )
        try:
            await coordinator.api.async_update_guest(str(guest["id"]), fields)
        except NimlyCloudError as err:
            raise HomeAssistantError(f"the cloud rejected the change: {err}") from err
        # The vendor answers a patch with an empty body; re-read so the reply
        # (and the account sensor) shows the guest as it is now.
        guests = await coordinator.api.async_guest_users(coordinator.location_id)
        coordinator.guest_users = guests
        coordinator.async_update_listeners()
        fresh = next(
            (item for item in guests if str(item.get("id")) == str(guest["id"])),
            guest,
        )
        return {"guest": guest_row(fresh), "changed": sorted(fields)}

    async def _delete_cloud_guest(call: ServiceCall) -> dict[str, Any]:
        from .sync import delete_access_resilient

        coordinator = _coordinator(hass, call.data.get("entry_id"))
        guests = await coordinator.api.async_guest_users(coordinator.location_id)
        guest = _find_guest(guests, call.data.get("user_id"), call.data.get("guest"))
        user_id = str(guest["id"])
        name = guest.get("name")

        # The vendor removes a deleted guest's accesses asynchronously. Delete
        # them first so the end state is deterministic: no code keeps working
        # while its owner has already left the guest list.
        removed: list[dict[str, Any]] = []
        for device in coordinator.home.get("devices") or []:
            device_id = str(device.get("id") or "")
            if not device_id:
                continue
            for access in coordinator.access.get(device_id, []):
                if str(access.get("userId")) != user_id:
                    continue
                await delete_access_resilient(
                    coordinator, device_id, user_id, str(access.get("type"))
                )
                removed.append(
                    {"device_id": device_id, "type": access.get("type")}
                )
        try:
            await coordinator.api.async_delete_guest(coordinator.location_id, user_id)
        except NimlyCloudError as err:
            raise HomeAssistantError(f"the cloud rejected the removal: {err}") from err

        # A link to an identity that no longer exists is drift; drop it loudly
        # so the catalog cannot claim a guest is still synced.
        forgot: dict[str, list[dict[str, Any]]] = {}
        for mirror in _mirrors(hass):
            links = await mirror.async_forget_cloud_identity(user_id)
            if links:
                forgot[mirror.entry.entry_id] = links
                await mirror.async_journal_note(
                    "cloud_identity_deleted", detail=str(name or user_id)
                )
        await coordinator.async_request_refresh()
        coordinator.guest_users = [
            item
            for item in coordinator.guest_users
            if str(item.get("id")) != user_id
        ]
        coordinator.async_update_listeners()
        return {
            "deleted": {"user_id": user_id, "name": name},
            "accesses": removed,
            "links_forgotten": forgot,
        }

    async def _set_cloud_code(call: ServiceCall) -> dict[str, Any]:
        from .sync import create_access_after_delete, delete_access_resilient

        coordinator = _coordinator(hass, call.data.get("entry_id"))
        guests = await coordinator.api.async_guest_users(coordinator.location_id)
        guest = _find_guest(guests, call.data.get("user_id"), call.data.get("guest"))
        user_id = str(guest["id"])
        access_type: str = call.data["type"]
        value: str = call.data["value"].strip()
        if access_type == "pin" and not re.fullmatch(r"\d{4,10}", value):
            raise HomeAssistantError("a PIN is 4-10 digits")
        if access_type == "tag" and not re.fullmatch(r"[0-9A-Fa-f]{4,32}", value):
            raise HomeAssistantError("a tag value is 4-32 hex characters")
        mirror, device_id = _lock_device(hass, coordinator, call.data.get("entry_id"))

        # When the identity is one of our own guests, teach the catalog the new
        # code before the vendor pushes it: the bridge binds the incoming write
        # by matching the code against the catalog, and it must find it there.
        slot = mirror.slot_for_cloud_user(user_id) if access_type == "pin" else None
        previous: str | None = None
        if slot is not None:
            previous = str((mirror.guests.get(str(slot)) or {}).get("code") or "")
            # The local side owns the value: write it into the lock and the
            # catalog before the cloud push, so the arriving write binds here
            # instead of adding a second copy of a code we already changed.
            await mirror.async_apply_guest_code(slot, value)

        # Read the device's accesses fresh: the polled copy can be a minute old,
        # and the delete-then-create decision depends on it.
        try:
            live = await coordinator.api.async_device_access(device_id)
        except NimlyCloudError:
            live = coordinator.access.get(device_id) or []
        exists = any(
            str(access.get("userId")) == user_id
            and str(access.get("type")) == access_type
            for access in live
        )
        try:
            if exists:
                # The vendor's PATCH waits for a module acknowledgement the
                # emulator does not speak, so a change is delete-then-create:
                # both legs are flows the module answers, and the end state is
                # deterministic.
                await delete_access_resilient(
                    coordinator, device_id, user_id, access_type
                )
            await create_access_after_delete(
                coordinator, device_id, user_id, access_type, value
            )
        except (NimlyCloudError, HomeAssistantError) as err:
            if slot is not None and previous:
                # Put the lock and the catalog back so neither claims a code
                # the vendor does not have. An empty previous value has no
                # write to restore.
                await mirror.async_apply_guest_code(slot, previous)
            if isinstance(err, HomeAssistantError):
                raise
            raise HomeAssistantError(f"the cloud rejected the code: {err}") from err

        await coordinator.async_request_refresh()
        return {
            "guest": guest.get("name"),
            "type": access_type,
            "created": not exists,
            "slot": slot,
        }

    async def _audit(call: ServiceCall) -> dict[str, Any]:
        from .sync import cloud_device_for

        cloud = _coordinator(hass, call.data.get("entry_id"))
        mirrors = _mirrors(hass)
        guests = await cloud.api.async_guest_users(cloud.location_id)
        ids = {str(guest.get("id")) for guest in guests if guest.get("id")}

        # Where every user's credentials actually live, per live device. The
        # vendor also keeps records for devices that no longer exist; those show
        # up as ghosts, because no lock can answer for them.
        devices = cloud.home.get("devices") or []
        live_ids = {str(device.get("id")) for device in devices if device.get("id")}
        device_names = {
            str(device.get("id")): str(device.get("name") or "") for device in devices
        }
        access_by_user: dict[str, dict[str, set[str]]] = {}
        live_types: dict[str, set[str]] = {}
        for device_id, access_list in cloud.access.items():
            if device_id not in live_ids:
                continue
            for access in access_list:
                user, kind = str(access.get("userId")), str(access.get("type"))
                if not user or not kind:
                    continue
                access_by_user.setdefault(user, {}).setdefault(device_id, set()).add(kind)
                live_types.setdefault(user, set()).add(kind)

        locks: list[dict[str, Any]] = []
        for mirror in mirrors:
            device_id = cloud_device_for(cloud, mirror)
            accesses = {
                (str(access.get("userId")), str(access.get("type")))
                for access in (cloud.access.get(device_id) or [])
            }
            local = mirror.cloud_sync_candidates()
            local_names = set()
            entries: list[dict[str, Any]] = []
            for candidate in local:
                local_names.add(candidate["name"].strip().casefold())
                user_id = candidate["user_id"]
                other_locks = sorted(
                    {
                        device_names.get(other, other)
                        for other in (access_by_user.get(user_id or "") or {})
                        if other != device_id
                    }
                )
                if not user_id or user_id not in ids:
                    state = "no_cloud_identity"
                elif device_id is None:
                    state = "no_cloud_device"
                elif (user_id, "pin") in accesses:
                    state = "ok"
                elif other_locks:
                    state = "synced_elsewhere"
                else:
                    state = "no_cloud_access"
                entries.append(
                    {
                        "slot": candidate["slot"],
                        "name": candidate["name"],
                        "state": state,
                        "other_locks": other_locks,
                    }
                )
            locks.append(
                {
                    "lock": mirror.entry.entry_id,
                    "device_id": device_id,
                    "guests": entries,
                    "cloud_only": sorted(
                        str(guest.get("name"))
                        for guest in guests
                        if str(guest.get("name") or "").strip().casefold()
                        not in local_names
                    ),
                }
            )

        ghosts: list[dict[str, Any]] = []
        for guest in guests:
            user = str(guest.get("id") or "")
            claimed = {
                kind
                for kind, flag in (
                    ("pin", guest.get("hasDoorlockPin")),
                    ("tag", guest.get("hasDoorlockTag")),
                    ("finger", guest.get("hasDoorlockFingerprint")),
                )
                if flag
            }
            missing = sorted(claimed - live_types.get(user, set()))
            if missing:
                ghosts.append({"name": guest.get("name"), "types": missing})
        return {"locks": locks, "cloud_guests": len(guests), "ghosts": ghosts}

    async def _sync_cloud(call: ServiceCall) -> dict[str, Any]:
        from ..mirror.coordinator import MirrorCoordinator
        from .sync import async_sync_lock

        cloud = _coordinator(hass, call.data.get("entry_id"))
        dry_run = bool(call.data["dry_run"])
        mirrors = [
            item
            for item in hass.data.get(DOMAIN, {}).values()
            if isinstance(item, MirrorCoordinator)
        ]
        actions: list[dict[str, Any]] = []
        for mirror in mirrors:
            lock_actions = await async_sync_lock(cloud, mirror, dry_run=dry_run)
            actions += lock_actions
            if dry_run:
                continue
            identities = sum(
                1
                for item in lock_actions
                if item["action"] in ("create_guest", "adopt_guest")
            )
            accesses = sum(
                1 for item in lock_actions if item["action"] == "create_access"
            )
            if identities or accesses:
                await mirror.async_journal_note(
                    "cloud_synced",
                    detail=f"{identities} identit(ies), {accesses} access(es)",
                )
        if not dry_run:
            await cloud.async_request_refresh()
        return {"dry_run": dry_run, "actions": actions}

    async def _restore_cloud(call: ServiceCall) -> dict[str, Any]:
        from ..mirror.coordinator import MirrorCoordinator
        from .sync import async_restore_lock

        cloud = _coordinator(hass, call.data.get("entry_id"))
        dry_run = bool(call.data["dry_run"])
        mirrors = [
            item
            for item in hass.data.get(DOMAIN, {}).values()
            if isinstance(item, MirrorCoordinator)
        ]
        reports = [
            await async_restore_lock(cloud, mirror, dry_run=dry_run)
            for mirror in mirrors
        ]
        if not dry_run:
            await cloud.async_request_refresh()
        return {"dry_run": dry_run, "locks": reports}

    async def _cleanup_cloud(call: ServiceCall) -> dict[str, Any]:
        from .maintenance import async_reconcile

        coordinator = _coordinator(hass, call.data.get("entry_id"))
        return await async_reconcile(
            hass, coordinator.entry, coordinator, dry_run=bool(call.data["dry_run"])
        )

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, _refresh, schema=SCHEMA_REFRESH,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FETCH_HISTORY, _fetch_history, schema=SCHEMA_FETCH_HISTORY,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_LOCK, _set_lock, schema=SCHEMA_SET_LOCK,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GATEWAY_SCAN, _gateway_scan, schema=SCHEMA_GATEWAY_SCAN,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PROBE, _probe, schema=SCHEMA_PROBE,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEANUP_CLOUD, _cleanup_cloud, schema=SCHEMA_CLEANUP_CLOUD,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLOUD_GUESTS, _cloud_guests, schema=SCHEMA_CLOUD_GUESTS,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_CLOUD, _sync_cloud, schema=SCHEMA_SYNC_CLOUD,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_AUDIT, _audit, schema=SCHEMA_AUDIT,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REPAIR_JOIN,
        _repair_join,
        schema=SCHEMA_REPAIR_JOIN,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LINK_CLOUD_GUEST,
        _link_cloud_guest,
        schema=SCHEMA_LINK_CLOUD_GUEST,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_CLOUD_GUEST,
        _update_cloud_guest,
        schema=SCHEMA_UPDATE_CLOUD_GUEST,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_CLOUD_GUEST,
        _delete_cloud_guest,
        schema=SCHEMA_DELETE_CLOUD_GUEST,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CLOUD_CODE,
        _set_cloud_code,
        schema=SCHEMA_SET_CLOUD_CODE,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESTORE_CLOUD, _restore_cloud, schema=SCHEMA_RESTORE_CLOUD,
        supports_response=SupportsResponse.ONLY,
    )
