"""Cloud registry identity: which lock an entity describes.

The vendor gives the lock a new device id every time it is registered again, while
the lock itself does not change: the module's IEEE address is the account's
serialNumber. Registry entries are therefore keyed on that serial, so a
re-registration updates the device instead of duplicating every entity with a
``_2`` suffix. Pure logic with no Home Assistant imports (the unit tests load it
by path).
"""

from __future__ import annotations

_SERIAL_LENGTH = 16
_HEX = set("0123456789abcdef")


def normalise_serial(value: object) -> str | None:
    """The 16 hex characters of a module address, or None for anything else."""
    text = str(value or "").replace(":", "").replace("-", "").strip().lower()
    if len(text) != _SERIAL_LENGTH or any(char not in _HEX for char in text):
        return None
    return text


def serial_from_identifiers(identifiers: object) -> str | None:
    """The module serial inside a device registry identifier set.

    ``identifiers`` is the registry's list of ``(domain, value)`` pairs; the
    value of a Zigbee device is its IEEE address in colon notation, which is the
    same address the vendor calls the serial number.
    """
    try:
        pairs = list(identifiers)  # type: ignore[arg-type]
    except TypeError:
        return None
    for pair in pairs:
        try:
            _domain, value = pair
        except (TypeError, ValueError):
            continue
        serial = normalise_serial(value)
        if serial:
            return serial
    return None


def split_unique_id(unique_id: object) -> tuple[str, str] | None:
    """Split ``identity_key`` into (identity, key).

    The identity is a vendor device uuid or a module serial and never contains an
    underscore, so the split is on the first one.
    """
    if not isinstance(unique_id, str) or "_" not in unique_id:
        return None
    identity, _, key = unique_id.partition("_")
    if not identity or not key:
        return None
    return identity, key


def is_vendor_uuid(identity: str) -> bool:
    """True for the vendor's dashed device id shape."""
    parts = identity.split("-")
    return len(identity) == 36 and [len(part) for part in parts] == [8, 4, 4, 4, 12]


def collision_rank(entity_id: str) -> tuple[int, int]:
    """Sort key preferring the entity without a ``_2`` collision suffix."""
    tail = entity_id.rsplit("_", 1)[-1]
    suffixed = tail.isdigit() and len(tail) <= 2
    return (1 if suffixed else 0, len(entity_id))
