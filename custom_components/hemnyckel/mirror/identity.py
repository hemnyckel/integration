"""Pure helpers for following a lock entity across a rename.

A lock entity id is not an identity: Home Assistant lets the household rename
the entity at any time, and the id changes while the Zigbee device behind it
(its serial) does not. These helpers turn a registry event into the followed
id and match a lock entity back by serial, so the coordinator can keep its ZHA
link attached across a rename.

No Home Assistant imports: the unit tests load this module by path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def normalise_serial(value: Any) -> str:
    """A serial as a bare lowercase key, so colon/dash/dot forms compare equal."""
    return str(value or "").replace(":", "").replace("-", "").replace(".", "").lower()


def zha_ieee(identifiers: Iterable[Iterable[Any]]) -> str | None:
    """The Zigbee IEEE from a device's ``(domain, value)`` identifiers."""
    for domain, value in identifiers:
        if domain == "zha" and value:
            return str(value).lower()
    return None


def renamed_entity_id(
    event_data: Mapping[str, Any], current_entity_id: str | None
) -> str | None:
    """The new entity id when a registry event renamed the one we follow.

    ``old_entity_id`` is only present when the id actually changed, so this is
    exactly the rename case; the payload's ``entity_id`` is the new id. Any
    other registry event returns ``None`` and is ignored.
    """
    if not current_entity_id or event_data.get("action") != "update":
        return None
    old = event_data.get("old_entity_id")
    new = event_data.get("entity_id")
    if not old or not new or old == new:
        return None
    if old != current_entity_id:
        return None
    return str(new)


def entity_id_on_serial(
    entities: Iterable[Mapping[str, Any]],
    device_values: Mapping[str, Iterable[str]],
    serial: str | None,
) -> str | None:
    """The first enabled lock entity whose device carries this serial.

    ``entities`` are entity-registry records (``entity_id``, ``domain``,
    ``device_id``, ``disabled_by``); ``device_values`` maps a device id to the
    identifier/connection values that name it. The serial is the stable key: it
    survives an entity rename, and the entry stores it once the lock is found.
    """
    wanted = normalise_serial(serial)
    if not wanted:
        return None
    for entity in entities:
        if entity.get("domain") != "lock" or entity.get("disabled_by"):
            continue
        values = device_values.get(str(entity.get("device_id") or "")) or ()
        if any(normalise_serial(value) == wanted for value in values):
            entity_id = entity.get("entity_id")
            return str(entity_id) if entity_id else None
    return None
