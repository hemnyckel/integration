"""Constants for hemnyckel — the local lock mirror and the ESP32 bridge.

The protocol towards the firmware (the bridge) is frozen; see the repository docs.
"""

from __future__ import annotations

from typing import Any

DOMAIN = "hemnyckel"

# --- Entry types -------------------------------------------------------------
CONF_TYPE = "type"
TYPE_MIRROR = "mirror"
TYPE_BRIDGE = "bridge"

# --- Configuration (the lock mirror) -----------------------------------------
CONF_LOCK_ENTITY = "lock_entity_id"
CONF_LOCK_NAME = "lock_name"
CONF_PREFIX = "topic_prefix"

# The bridge (kit) a mirror entry is bound to, as 12 hex characters: the MAC the
# wizard matched from the bridge's retained <prefix>/info identity.
CONF_BRIDGE = "bridge"
CONF_CHANNELS = "channels"
CONF_ENABLED = "enabled"
CONF_ADDRESS = "address"
CONF_IEEE = "ieee"

# A name (and area) the user gave the lock's module in Home Assistant; re-applied
# when the same serial joins again, so a re-pair comes back named correctly.
CONF_DEVICE_IDENTITY = "device_identity"
CONF_ENDPOINT = "endpoint_id"

DEFAULT_PREFIX = "nimly/proxy"
DEFAULT_ENDPOINT = 11

# --- Channels (toggles in the UI) ------------------------------------------
CH_LOCK = "lock"
CH_ACTIVITY = "activity"
CH_PIN = "pin"
CH_FINGERPRINT = "fingerprint"
CH_VOLUME = "volume"
CH_AUTOLOCK = "autolock"
CH_BATTERY = "battery"
CH_SYNC = "sync"
CH_RECONCILE = "slot_reconcile"
CH_NAMES = "names"

CHANNELS: list[str] = [
    CH_LOCK,
    CH_ACTIVITY,
    CH_PIN,
    CH_FINGERPRINT,
    CH_VOLUME,
    CH_AUTOLOCK,
    CH_BATTERY,
    CH_SYNC,
    CH_RECONCILE,
    CH_NAMES,
]

DEFAULT_CHANNELS: dict[str, bool] = {
    CH_LOCK: True,
    CH_ACTIVITY: True,
    CH_PIN: True,
    CH_FINGERPRINT: True,
    CH_VOLUME: True,
    CH_AUTOLOCK: True,
    CH_BATTERY: True,
    CH_SYNC: True,
    CH_RECONCILE: False,
    CH_NAMES: False,
}

# Display names for the channel switches (the user's "sliders")
CHANNEL_LABELS: dict[str, str] = {
    CH_LOCK: "Mirror lock/unlock",
    CH_ACTIVITY: "Mirror notifications",
    CH_PIN: "Mirror PIN codes",
    CH_FINGERPRINT: "Mirror fingerprints",
    CH_VOLUME: "Mirror sound volume",
    CH_AUTOLOCK: "Mirror auto-lock",
    CH_BATTERY: "Mirror battery",
    CH_SYNC: "Sync on start",
    CH_RECONCILE: "Slot sync (lock → app)",
    CH_NAMES: "Mirror user names",
}

CHANNEL_ICONS: dict[str, str] = {
    CH_LOCK: "mdi:lock-sync",
    CH_ACTIVITY: "mdi:bell-ring-outline",
    CH_PIN: "mdi:dialpad",
    CH_FINGERPRINT: "mdi:fingerprint",
    CH_VOLUME: "mdi:volume-high",
    CH_AUTOLOCK: "mdi:timer-lock-outline",
    CH_BATTERY: "mdi:battery-sync",
    CH_SYNC: "mdi:sync",
    CH_RECONCILE: "mdi:format-list-sync",
    CH_NAMES: "mdi:account-edit",
}

PRESET_FULL = "full"
PRESET_NOTIFICATIONS = "notifications"
PRESET_HA_ONLY = "ha_only"
PRESET_CUSTOM = "custom"

PRESETS: dict[str, dict[str, bool]] = {
    PRESET_FULL: {
        CH_LOCK: True,
        CH_ACTIVITY: True,
        CH_PIN: True,
        CH_FINGERPRINT: True,
        CH_VOLUME: True,
        CH_AUTOLOCK: True,
        CH_BATTERY: True,
        CH_SYNC: True,
        CH_RECONCILE: False,
        CH_NAMES: False,
    },
    PRESET_NOTIFICATIONS: {
        CH_LOCK: False,
        CH_ACTIVITY: True,
        CH_PIN: False,
        CH_FINGERPRINT: False,
        CH_VOLUME: False,
        CH_AUTOLOCK: False,
        CH_BATTERY: True,
        CH_SYNC: False,
        CH_RECONCILE: False,
        CH_NAMES: False,
    },
    PRESET_HA_ONLY: {
        CH_LOCK: True,
        CH_ACTIVITY: False,
        CH_PIN: False,
        CH_FINGERPRINT: False,
        CH_VOLUME: False,
        CH_AUTOLOCK: False,
        CH_BATTERY: False,
        CH_SYNC: False,
        CH_RECONCILE: False,
        CH_NAMES: False,
    },
}

# --- MQTT topics (relative to the prefix) ----------------------------------
TOPIC_HA_TO_BRIDGE = "ha_to_bridge"
TOPIC_BRIDGE_TO_HA = "bridge_to_ha"
TOPIC_STATE = "state"
TOPIC_BATTERY = "battery"
TOPIC_PIN = "pin"
TOPIC_OTA = "ota"
TOPIC_INFO = "info"

# The legacy shared identity topic (firmware 0.5.x). Firmware 0.6.0 and later
# announces on its own "<prefix>/info"; discovery listens on the wildcard
# nimly/+/info plus this topic for the transition.
TOPIC_BRIDGE_INFO = "nimly/info"

# --- Event names used in the protocol ---------------------------------------
EV_STATE = "state"
EV_BATTERY = "battery"
EV_ACTION = "action"
EV_PIN_SET = "pin_set"
EV_PIN_CLEAR = "pin_clear"
EV_FP_ENROLL = "fp_enroll"
EV_TAG_SCAN = "tag_scan"
EV_TAG_CLEAR = "tag_clear"
EV_FP_CLEAR = "fp_clear"
# Settings the vendor app writes straight to the module (ZCL Write Attributes).
EV_VOLUME = "volume"
EV_AUTOLOCK = "autolock"

CMD_LOCK = "lock"
CMD_UNLOCK = "unlock"
CMD_GET_STATE = "get_state"
CMD_FACTORY_RESET = "factory_reset"
CMD_VOLUME = "volume"
CMD_AUTOLOCK = "autolock"
CMD_BATTERY = "battery"
CMD_EVENT = "event"
CMD_OTA = "ota"

# OTA-manifest: {"version": "…", "builds": {"esp32": "<app-bin>.bin", …}}.
# Filenames are relative to the manifest URL. The user can repoint them in options.
CONF_OTA_MANIFEST_URL = "ota_manifest_url"
DEFAULT_OTA_MANIFEST_URL = (
    "https://raw.githubusercontent.com/hemnyckel/integration/main/firmware/webflash/ota.json"
)
MANIFEST_REFRESH = 1800  # seconds between fetches of the OTA manifest

# --- ZCL -------------------------------------------------------------------
ZCL_CLUSTER_DOORLOCK = 0x0101
ZCL_CMD_SET_PIN = 0x05
ZCL_CMD_GET_PIN = 0x06
ZCL_CMD_CLEAR_PIN = 0x07
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
ACTION_FROM_NAME: dict[str, int] = {v: k for k, v in ACTION_NAMES.items()}
SOURCE_FROM_NAME: dict[str, int] = {v: k for k, v in SOURCE_NAMES.items()}

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
HEALTH_INTERVAL = 60  # seconds between get_state pings
HEALTH_TIMEOUT = 150  # seconds without state -> offline
HELLO_GAP = 90  # a longer gap between hellos means the emulator rebooted
ECHO_WINDOW = 8.0  # seconds a mirrored command suppresses its echo

# --- Shared events ----------------------------------------------------------
# Fired for every new journal entry (access and admin events), so automations can
# react to "who opened the door" without polling the journal.
EVENT_JOURNAL = "hemnyckel_door_event"

