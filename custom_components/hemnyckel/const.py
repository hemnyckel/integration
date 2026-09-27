"""Constants for hemnyckel — a local-only integration for Nimly locks.

The lock lives on ZHA; everything here supports that local path. The vendor
app side (the bridge and the emulator) has been removed.
"""

from __future__ import annotations

from typing import Any

DOMAIN = "hemnyckel"

# --- Entry types -------------------------------------------------------------
CONF_TYPE = "type"
TYPE_MIRROR = "mirror"

# --- Configuration (the lock) ------------------------------------------------
CONF_LOCK_ENTITY = "lock_entity_id"

# A name (and area) the user gave the lock's module in Home Assistant; re-applied
# when the same serial joins again, so a re-pair comes back named correctly.
CONF_DEVICE_IDENTITY = "device_identity"

DEFAULT_ENDPOINT = 11

# --- ZCL -------------------------------------------------------------------
ZCL_CLUSTER_DOORLOCK = 0x0101
ZCL_CMD_FP_ENROLL = 0x71
ZCL_CMD_TAG_SCAN = 0x70
ZCL_CMD_TAG_CLEAR = 0x18
ZCL_CMD_FP_CLEAR = 0x72

ZHA_SERVICE = "issue_zigbee_cluster_command"

# --- Actions and sources (0x0100) ------------------------------------------
ACTION_LOCK = 0x01
ACTION_UNLOCK = 0x02

SRC_ZIGBEE = 0x00
SRC_KEYPAD = 0x02
SRC_FINGERPRINT = 0x03
SRC_RFID = 0x04
SRC_UNATTRIBUTED = 0x05
SRC_AUTO = 0x0A

ACTION_NAMES: dict[int, str] = {
    ACTION_LOCK: "lock",
    ACTION_UNLOCK: "unlock",
}
SOURCE_NAMES: dict[int, str] = {
    SRC_ZIGBEE: "zigbee",
    SRC_KEYPAD: "keypad",
    SRC_FINGERPRINT: "fingerprint",
    SRC_RFID: "rfid",
    SRC_UNATTRIBUTED: "unattributed",
    SRC_AUTO: "auto",
}

# Sources that count as "a person did something" (for notifications)
HUMAN_SOURCES = {SRC_KEYPAD, SRC_FINGERPRINT, SRC_RFID, SRC_ZIGBEE, SRC_UNATTRIBUTED}

# --- Slots -------------------------------------------------------------------
DEFAULT_SLOT: dict[str, Any] = {
    "name": "",
    "has_pin": False,
    "has_fingerprint": False,
    "has_rfid": False,
    # A fingerprint is only trusted once a finger actually opened the door with
    # it; an enrollment proves nothing (the lock reports nothing while it runs).
    "finger_used": False,
}


def decode_operation_event(value: int) -> dict[str, Any] | None:
    """Decode attribute 0x0100 (bitmap32): slot, action and source.

    Bits 0-15 are the slot (0 means none), 16-23 the action, 24-31 the source.
    A slot 0 with a human source (keypad, fingerprint, rfid) is the master
    credential; with an automatic source it means no user at all. Unknown
    codes come back as None names rather than a guess.
    """
    if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        return None
    slot = value & 0xFFFF
    action_code = (value >> 16) & 0xFF
    source_code = (value >> 24) & 0xFF
    master = slot == 0 and source_code in (SRC_KEYPAD, SRC_FINGERPRINT, SRC_RFID)
    return {
        "user_slot": slot if (slot > 0 or master) else None,
        "master": master,
        "action_code": action_code,
        "source_code": source_code,
        "action": ACTION_NAMES.get(action_code),
        "source": SOURCE_NAMES.get(source_code),
    }

# --- Health ----------------------------------------------------------------
HEALTH_INTERVAL = 60  # seconds between ZHA health ticks

# --- Shared events ----------------------------------------------------------
# Fired for every new journal entry (access and admin events), so automations can
# react to "who opened the door" without polling the journal.
EVENT_JOURNAL = "hemnyckel_door_event"
