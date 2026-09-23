"""Constants for nimly — the vendor account and the emulator mirror in one integration.

The protocol towards the firmware (the bridge) is frozen; see the repository docs.
"""

from __future__ import annotations

from typing import Any

DOMAIN = "nimly"

# --- Entry types -------------------------------------------------------------
CONF_TYPE = "type"
TYPE_CLOUD = "cloud"
TYPE_MIRROR = "mirror"
TYPE_BRIDGE = "bridge"

# --- Configuration (the lock mirror) -----------------------------------------
CONF_LOCK_ENTITY = "lock_entity_id"
CONF_LOCK_NAME = "lock_name"
CONF_PREFIX = "topic_prefix"
CONF_CHANNELS = "channels"
CONF_ENABLED = "enabled"
CONF_ADDRESS = "address"
CONF_IEEE = "ieee"
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
CH_CLOUD = "cloud"

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
    CH_CLOUD,
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
    CH_CLOUD: True,
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
    CH_CLOUD: "Cloud sync",
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
    CH_CLOUD: "mdi:cloud-sync",
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

# Well-known retained topic where the bridge announces itself (prefix, id, firmware).
# Sits outside the prefix so the wizard can find the emulator without knowing it.
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
    "https://raw.githubusercontent.com/c14ym0re/nimly/main/firmware/webflash/ota.json"
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
# Fired when the lock itself reports an activity (button, keypad, app command).
EVENT_NIMLY_LOCK_ACTIVITY = "nimly_lock_activity"
# Fired by the cloud layer for every activity the vendor attributes. The mirror listens so
# the local view gains who/how when the lock itself cannot report it over Zigbee.
EVENT_NIMLY_CLOUD_ACTIVITY = "nimly_cloud_activity"
# Fired for every new journal entry (access and admin events), so automations can
# react to "who opened the door" without polling the journal.
EVENT_JOURNAL = "nimly_journal_entry"

# --- The vendor account (cloud) ---------------------------------------------
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_LOCATION_ID = "location_id"
CONF_COMPANY_ID = "company_id"
CONF_SCAN_INTERVAL = "scan_interval"

# Public constants from the vendor app: the same values the official app uses.
API_URL = "https://api-neutralclone.iotiliti.cloud"
API_CLIENT_ID = "account"
API_CLIENT_SECRET = "55c78905-7601-48fa-b589-2d15c4ad60e7"

DEFAULT_SCAN_INTERVAL = 30  # seconds between polls
TOKEN_REFRESH_MARGIN = 120  # refresh this many seconds before expiry

# --- Endpoints --------------------------------------------------------------
# Everything the integration calls. The history endpoint needs the location's
# company identifier in a request header, which the vendor's own app also does.
PATH_TOKEN = "/oauth/v2/token"
PATH_REFRESH = "/oauth/v2/refresh-token"
PATH_ME = "/users/me"
PATH_LOCATIONS = "/locations"
PATH_LOCATION_USERS = "/locations/{location_id}/users"
PATH_LOCATION_USER = "/locations/{location_id}/users/{user_id}"
PATH_HOME = "/home/{location_id}"
PATH_DEVICE = "/devices/{device_id}"
PATH_DEVICE_ACCESS = "/devices/{device_id}/access"
PATH_DEVICE_HISTORY = "/devices/{device_id}/features-history"
PATH_DEVICE_LOCK = "/devices/{device_id}/lock"
PATH_DEVICE_SETTINGS = "/devices/{device_id}/settings"
PATH_DEVICE_ACTION = "/devices/{device_id}/action"
PATH_GATEWAY_ACTION = "/gateways/{gateway_id}/action"
PATH_GATEWAY_SCAN = "/gateways/{gateway_id}/scan"
PATH_GUEST_USERS = "/guest-users"
PATH_GUEST_USER = "/guest-users/{user_id}"
PATH_DEVICE_SCAN = "/devices/{device_id}/access/scan-tag"

HEADER_COMPANY_ID = "companyId"

# --- The cloud's feature state ----------------------------------------------
# A device state is addressed as <feature>_<key>, for example report_event.
FEATURE_LOCK = "lock"
FEATURE_REPORT = "report"
FEATURE_BATTERY = "battery"
FEATURE_PINS = "pins"
FEATURE_TAGS = "tags"
FEATURE_DIAGNOSTIC = "diagnostic"

STATE_LOCK_STATE = "state"
STATE_AUTORELOCK = "autorelocktime"
STATE_VOLUME = "soundvolume"

# Feature states that can appear in the history feed, with how they are read.
HISTORY_REPORT_EVENT = "report_event"
HISTORY_LOCK_STATE = "lock_state"

# --- The vendor's device settings -------------------------------------------
SETTING_AUTOLOCK = "autolock"
SETTING_VOLUME = "volume"
SETTING_MASTER_PIN_MODE = "masterpinmode"
SETTING_PIN_REQUIRED_REMOTE = "pinRequiredUnlockRemote"
SETTING_PART_OF_ALARM = "partofalarm"
SETTING_DEVICE_TYPE = "deviceType"

# The vendor stores volume as a number 0-100 and as a name.
VOLUME_NAMES = {0: "silent", 36: "low", 72: "medium", 100: "high"}

# --- Event vocabulary (report.event.value) ----------------------------------
# Sources use the same names as the mirror layer, so an activity can travel between the
# layers without a translation table.
#
# Values marked "confirmed" have been observed from a real lock whose module sits on the
# vendor bridge; the rest come from the vendor app's own vocabulary. parse_event() reads an
# unfamiliar value structurally, so a firmware addition never becomes a silent None.
EVENT_UNLOCKED_BY_PIN = "DOORLOCK_UNLOCKED_BY_PIN"  # confirmed
EVENT_UNLOCKED_BY_FINGER = "DOORLOCK_UNLOCKED_BY_FINGER"  # confirmed
EVENT_LOCKED_FROM_APP = "DOORLOCK_LOCKED_FROM_APP"  # confirmed
EVENT_UNLOCK_WITH_PIN = "DOORLOCK_UNLOCK_WITH_PIN"
EVENT_UNLOCK_WITH_FINGERPRINT = "DOORLOCK_UNLOCK_WITH_FINGERPRINT"
EVENT_LOCK_WITH_TAG = "DOORLOCK_LOCK_WITH_TAG"
EVENT_UNLOCKED_FROM_APP = "DOORLOCK_UNLOCKED_FROM_APP"
EVENT_LOCKED_MANUALLY = "DOORLOCK_LOCKED_MANUALLY"
EVENT_AUTO_LOCKED = "DOORLOCK_AUTO_LOCKED"

CONFIRMED_EVENTS = frozenset(
    {EVENT_UNLOCKED_BY_PIN, EVENT_UNLOCKED_BY_FINGER, EVENT_LOCKED_FROM_APP}
)

EVENT_MAP: dict[str, tuple[str, str]] = {
    EVENT_UNLOCKED_BY_PIN: ("unlock", "keypad"),
    EVENT_UNLOCKED_BY_FINGER: ("unlock", "fingerprint"),
    EVENT_LOCKED_FROM_APP: ("lock", "zigbee"),
    EVENT_UNLOCK_WITH_PIN: ("unlock", "keypad"),
    EVENT_UNLOCK_WITH_FINGERPRINT: ("unlock", "fingerprint"),
    EVENT_LOCK_WITH_TAG: ("lock", "rfid"),
    EVENT_UNLOCKED_FROM_APP: ("unlock", "zigbee"),
    EVENT_LOCKED_MANUALLY: ("lock", "unattributed"),
    EVENT_AUTO_LOCKED: ("lock", "auto"),
}

EVENT_PREFIX = "DOORLOCK_"

# Token -> source, checked in order. App commands reach the module over Zigbee, so "from
# app" is source zigbee — the source code the lock itself reports.
_TOKEN_SOURCES: tuple[tuple[str, str], ...] = (
    ("FINGER", "fingerprint"),
    ("PIN", "keypad"),
    ("CODE", "keypad"),
    ("TAG", "rfid"),
    ("RFID", "rfid"),
    ("APP", "zigbee"),
    ("AUTO", "auto"),
    ("MANUAL", "unattributed"),
    ("ZIGBEE", "zigbee"),
)


def parse_event(value: str | None) -> tuple[str | None, str | None]:
    """Turn a cloud event value into (action, source).

    Known values come from EVENT_MAP. Anything else with the DOORLOCK_ prefix is read
    structurally so a firmware addition still produces a usable action and a best-effort
    source rather than a silent None.
    """
    if not value:
        return None, None
    if value in EVENT_MAP:
        return EVENT_MAP[value]

    text = value.upper()
    if not text.startswith(EVENT_PREFIX):
        return None, None

    # Strip the prefix first: "DOORLOCK_" itself contains LOCK and would otherwise make
    # every unrecognised event look like a lock.
    body = text[len(EVENT_PREFIX) :]
    if "UNLOCK" in body:
        action = "unlock"
    elif "LOCK" in body:
        action = "lock"
    else:
        action = None

    source = "unattributed"
    for token, name in _TOKEN_SOURCES:
        if token in body:
            source = name
            break
    return action, source


# --- Services ---------------------------------------------------------------
SERVICE_PROBE = "probe"
SERVICE_REFRESH = "refresh"
SERVICE_FETCH_HISTORY = "fetch_history"
SERVICE_SET_LOCK = "set_lock"
SERVICE_GATEWAY_SCAN = "gateway_scan"
SERVICE_CLEANUP_CLOUD = "cleanup_cloud"
SERVICE_CLOUD_GUESTS = "cloud_guests"
SERVICE_SYNC_CLOUD = "sync_cloud"
SERVICE_AUDIT = "audit"
SERVICE_RESTORE_CLOUD = "restore_cloud"
SERVICE_UPDATE_CLOUD_GUEST = "update_cloud_guest"
SERVICE_DELETE_CLOUD_GUEST = "delete_cloud_guest"
SERVICE_SET_CLOUD_CODE = "set_cloud_code"
SERVICE_LINK_CLOUD_GUEST = "link_cloud_guest"
SERVICE_REPAIR_JOIN = "repair_join"
