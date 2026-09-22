"""HA → cloud reconciliation.

Level one of the policy in docs/cloud-sync.md: for every local credential whose
value — or whose fingerprint template — the lock really holds, make sure the
cloud has the identity and the access. Uncertain cases are proposed, never
guessed. Everything here is idempotent: a re-run plans nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from .coordinator import NimlyCloudCoordinator
from .guests import match_guest, same_named, validity

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
        adopted = match_guest(name, guests)
        if adopted is not None:
            user_id = str(adopted.get("id") or "") or None
            note("adopt_guest", user_id=user_id)
            if not dry_run and user_id:
                await mirror.async_set_cloud_user(slot, user_id)
        else:
            if same_named(name, guests):
                # A same-named guest already holds an access: two people, one name.
                # A human decides; never guess.
                note("conflict")
                return actions
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
