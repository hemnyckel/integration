"""HA → cloud reconciliation.

Level one of the policy in docs/cloud-sync.md: for every local credential whose
value — or whose fingerprint template — the lock really holds, make sure the
cloud has the identity and the access. Uncertain cases are proposed, never
guessed. Everything here is idempotent: a re-run plans nothing.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .api import NimlyCloudError
from .coordinator import NimlyCloudCoordinator
from .guests import choose_identity, update_fields, validity

_LOGGER = logging.getLogger(__name__)


def cloud_device_for(cloud: NimlyCloudCoordinator, mirror: Any) -> str | None:
    """The vendor device whose serial is this mirror's module."""
    mine = (
        str(getattr(mirror, "ieee", "") or "").replace(":", "").replace("-", "").lower()
    )
    if len(mine) != 16:
        return None
    for device in cloud.devices:
        if cloud.device_serial(device.get("id")) == mine:
            return str(device.get("id"))
    return None


def _access_set(cloud: NimlyCloudCoordinator, device_id: str) -> set[tuple[str, str]]:
    return {
        (str(access.get("userId")), str(access.get("type")))
        for access in (cloud.access.get(device_id) or [])
    }


async def async_sync_lock(
    cloud: NimlyCloudCoordinator, mirror: Any, *, dry_run: bool
) -> list[dict[str, Any]]:
    """Reconcile every syncable guest of one lock; returns the plan or result."""
    device_id = cloud_device_for(cloud, mirror)
    if device_id is None:
        return []
    guests = await cloud.api.async_guest_users(cloud.location_id)
    by_id = {str(guest.get("id")): guest for guest in guests if guest.get("id")}
    accesses = _access_set(cloud, device_id)

    actions: list[dict[str, Any]] = []
    for candidate in mirror.cloud_sync_candidates():
        actions += await _async_sync_candidate(
            cloud,
            mirror,
            candidate,
            device_id,
            guests,
            by_id,
            accesses,
            dry_run=dry_run,
        )
    return actions


async def async_sync_guest(
    cloud: NimlyCloudCoordinator,
    mirror: Any,
    candidate: dict[str, Any],
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Reconcile one guest; ``candidate`` is a ``cloud_sync_candidates`` row."""
    device_id = cloud_device_for(cloud, mirror)
    if device_id is None:
        return []
    guests = await cloud.api.async_guest_users(cloud.location_id)
    by_id = {str(guest.get("id")): guest for guest in guests if guest.get("id")}
    accesses = _access_set(cloud, device_id)
    return await _async_sync_candidate(
        cloud, mirror, candidate, device_id, guests, by_id, accesses, dry_run=dry_run
    )


async def delete_access_resilient(
    cloud: NimlyCloudCoordinator, device_id: str, user_id: str, access_type: str
) -> None:
    """Delete an access, retrying once through the vendor's gateway flake.

    The vendor sometimes answers "gateway is offline" from a stale check even
    while the gateway is connected; a second attempt, moments later, goes
    through. A missing access (404) is not an error — the wanted end state is
    already true.
    """
    try:
        await cloud.api.async_delete_access(device_id, user_id, access_type)
    except NimlyCloudError as err:
        text = str(err)
        if "404" in text:
            return
        if "offline" not in text.lower():
            raise HomeAssistantError(f"could not remove the access: {err}") from err
        await asyncio.sleep(5)
        try:
            await cloud.api.async_delete_access(device_id, user_id, access_type)
        except NimlyCloudError as second:
            raise HomeAssistantError(
                f"could not remove the access: {second}"
            ) from second


async def create_access_after_delete(
    cloud: NimlyCloudCoordinator,
    device_id: str,
    user_id: str,
    access_type: str,
    value: str,
) -> None:
    """Create the access, retrying while the vendor's delete settles.

    The vendor processes an access deletion asynchronously (the module has to
    answer first), so an immediate create can still see the old record and
    answer 409 "already exists". Re-read and retry instead of failing a change
    that is really only waiting.
    """
    for attempt in range(6):
        try:
            await cloud.api.async_create_access(device_id, user_id, access_type, value)
            return
        except NimlyCloudError as err:
            if "already exists" not in str(err).lower() or attempt == 5:
                raise
            await asyncio.sleep(3)
            await cloud.async_request_refresh()
            try:
                live = await cloud.api.async_device_access(device_id)
                cloud.access[device_id] = live
            except NimlyCloudError:
                pass


async def async_push_guest_update(
    cloud: NimlyCloudCoordinator,
    mirror: Any,
    slot: int,
    changes: dict[str, Any],
) -> list[dict[str, Any]]:
    """Push a local guest edit (name, expiry, code) to the cloud.

    Only for a guest the cloud already knows: creation is ``async_sync_guest``'s
    job, and a guest the cloud never saw has nothing to sync. The caller (the
    mirror) journals both outcomes and never lets a vendor hiccup fail the
    local edit.
    """
    user_id = str(mirror.cloud_user(slot) or "")
    if not user_id:
        return []
    device_id = cloud_device_for(cloud, mirror)
    guests = await cloud.api.async_guest_users(cloud.location_id)
    by_id = {str(guest.get("id")): guest for guest in guests if guest.get("id")}
    if user_id not in by_id:
        # The remembered identity is gone; the reconcile path re-creates it.
        return []
    entry_id = mirror.entry.entry_id
    actions: list[dict[str, Any]] = []

    fields = update_fields(by_id[user_id], changes)
    if fields:
        await cloud.api.async_update_guest(user_id, fields)
        # The vendor answers a patch with an empty body; re-read so the account
        # sensor and the card show the guest as it is now.
        cloud.guest_users = await cloud.api.async_guest_users(cloud.location_id)
        cloud.async_update_listeners()
        actions.append(
            {
                "lock": entry_id,
                "slot": slot,
                "action": "update_guest",
                "changed": sorted(fields),
            }
        )

    value = str(changes.get("code") or "")
    if "code" in changes and device_id and re.fullmatch(r"\d{4,10}", value):
        # Replace the access instead of patching it: the vendor's PATCH waits
        # for a module acknowledgement the emulator does not speak, while the
        # delete/create pair are flows the module answers.
        live = await cloud.api.async_device_access(device_id)
        exists = any(
            str(access.get("userId")) == user_id
            and str(access.get("type")) == "pin"
            for access in live
        )
        if exists:
            await delete_access_resilient(cloud, device_id, user_id, "pin")
        await create_access_after_delete(cloud, device_id, user_id, "pin", value)
        actions.append(
            {"lock": entry_id, "slot": slot, "action": "set_access", "type": "pin"}
        )
    return actions


async def async_wipe_guest_users(
    cloud: NimlyCloudCoordinator, *, dry_run: bool
) -> dict[str, Any]:
    """Remove every guest identity of the account and the accesses they hold.

    The app has no bulk delete: each identity goes the same way the app's own
    "delete guest" does — accesses first (on every device), then the user. The
    report names exactly what a real run deleted.
    """
    guests = await cloud.api.async_guest_users(cloud.location_id)
    devices = [
        str(device.get("id"))
        for device in (cloud.home.get("devices") or [])
        if device.get("id")
    ]
    report: dict[str, Any] = {"dry_run": dry_run, "users": [], "errors": []}
    for guest in guests:
        user_id = str(guest.get("id") or "")
        if not user_id:
            continue
        role = str(guest.get("role") or "")
        if role and role != "GUEST_USER":
            # This endpoint should only ever list guests; belt and braces, the
            # owner's own account is not ours to remove.
            continue
        name = guest.get("name")
        report["users"].append({"id": user_id, "name": name})
        if dry_run:
            continue
        for device_id in devices:
            for access_type in ("pin", "tag", "finger"):
                try:
                    await delete_access_resilient(cloud, device_id, user_id, access_type)
                except Exception as err:  # noqa: BLE001 - the wipe reports and goes on
                    report["errors"].append(f"{name} {access_type}: {err}")
        try:
            await cloud.api.async_delete_guest(cloud.location_id, user_id)
        except Exception as err:  # noqa: BLE001 - same: report, never abort the sweep
            report["errors"].append(f"{name} delete: {err}")
    if not dry_run:
        await cloud.async_request_refresh()
    return report


async def _async_sync_candidate(
    cloud: NimlyCloudCoordinator,
    mirror: Any,
    candidate: dict[str, Any],
    device_id: str,
    guests: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    accesses: set[tuple[str, str]],
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    entry_id = mirror.entry.entry_id
    slot = candidate["slot"]
    name = candidate["name"]

    def note(action: str, **extra: Any) -> None:
        actions.append({"lock": entry_id, "slot": slot, "name": name, "action": action, **extra})

    user_id = candidate.get("user_id")
    if user_id and user_id not in by_id:
        user_id = None  # the remembered identity is gone; a new one is needed

    if user_id is None:
        on_this_lock = {user for user, _type in accesses}
        linked = {
            value
            for value in (
                {mirror.cloud_user(slot)} | set(mirror.cloud_links().values())
            )
            if value
        }
        action, adopted_id = choose_identity(
            name, guests, on_this_lock=on_this_lock, linked=linked
        )
        if action == "conflict":
            # Two people, one name — or a same-named identity already holding a
            # code here that is not recorded as ours. A human decides.
            note("conflict")
            return actions
        if action == "adopt":
            user_id = adopted_id or None
            note("adopt_guest", user_id=user_id)
            if not dry_run and user_id:
                await mirror.async_set_cloud_user(slot, user_id)
        else:
            note("create_guest")
            if not dry_run:
                start, end = validity()
                created = await cloud.api.async_create_guest(
                    name, cloud.location_id, start, end
                )
                user_id = str(created.get("id") or "") or None
                if user_id:
                    by_id[user_id] = created
                    await mirror.async_set_cloud_user(slot, user_id)
        if not user_id:
            return actions

    if (user_id, "pin") in accesses:
        note("in_sync")
        return actions
    note("create_access")
    if not dry_run:
        await cloud.api.async_create_access(
            device_id, user_id, "pin", candidate["code"]
        )
        accesses.add((user_id, "pin"))
    return actions


async def async_restore_lock(
    cloud: NimlyCloudCoordinator, mirror: Any, *, dry_run: bool
) -> dict[str, Any]:
    """Replay the catalog onto a fresh cloud device (docs/cloud-sync.md).

    PINs we hold the value for are synced; a linked fingerprint is re-recorded
    through a simulated enrollment (the lock holds the template) unless its slot
    no longer shows one. A PIN or tag we do not hold a value for cannot be
    re-created and is reported for the guided round instead of being guessed.
    """
    device_id = cloud_device_for(cloud, mirror)
    result: dict[str, Any] = {
        "lock": mirror.entry.entry_id,
        "actions": [],
        "cannot_restore": [],
    }
    if device_id is None:
        result["cannot_restore"].append({"reason": "no_cloud_device"})
        return result

    result["actions"] += await async_sync_lock(cloud, mirror, dry_run=dry_run)

    guests = await cloud.api.async_guest_users(cloud.location_id)
    identities = {str(guest.get("id")) for guest in guests if guest.get("id")}
    try:
        live = await cloud.api.async_device_access(device_id)
    except Exception:  # noqa: BLE001
        live = cloud.access.get(device_id) or []
    accesses = {(str(a.get("userId")), str(a.get("type"))) for a in live}

    for key, user_id in sorted(mirror.cloud_links().items()):
        slot_text, _, access_type = key.partition(":")
        if not slot_text.isdigit() or not access_type:
            continue
        slot = int(slot_text)
        if user_id not in identities:
            result["cannot_restore"].append(
                {"slot": slot, "type": access_type, "reason": "identity_gone"}
            )
            continue
        if (user_id, access_type) in accesses:
            continue
        if access_type == "finger":
            if not mirror.slots.finger_confirmed(slot):
                # Nobody has ever opened the door with this finger, so the lock
                # cannot be shown to hold its template; never simulate a lie.
                result["cannot_restore"].append(
                    {"slot": slot, "type": access_type, "reason": "finger_unconfirmed"}
                )
                continue
            result["actions"].append(
                {"lock": result["lock"], "slot": slot, "action": "restore_finger"}
            )
            if not dry_run:
                mirror.note_simulated_enroll()
                await cloud.api.async_enroll_finger(device_id, user_id)
                await mirror.async_journal_note(
                    "cloud_restored", detail=f"slot {slot} finger"
                )
        else:
            # The cloud needs the value and only the guest has it.
            result["cannot_restore"].append(
                {"slot": slot, "type": access_type, "reason": "value_unknown"}
            )
    return result
