"""Coordinator for the cloud layer.

Polls the account and turns the vendor's own activity feed into Home Assistant events.

The feed (`/devices/{id}/features-history`) is the source of truth for *who* did something:
the vendor records a `report_event` entry for the lock's action and, next to it, a
`lock_state` entry carrying the `userId` and `userName` the cloud attributed it to. This
module pairs the two and publishes one activity per action.

Timestamps from the vendor do not agree with UTC, so every activity carries both the
vendor's timestamp and the moment Home Assistant observed it. The observed time is the
reliable one; the skew is exposed for diagnostics.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NimlyCloudApi, NimlyCloudAuthError, NimlyCloudError
from ..const import (
    CONF_SCAN_INTERVAL,
    CONFIRMED_EVENTS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_NIMLY_CLOUD_ACTIVITY,
    HISTORY_LOCK_STATE,
    parse_event,
)

_LOGGER = logging.getLogger(__name__)

# How close in time a lock_state entry must be to attribute an action to a person.
ATTRIBUTION_WINDOW = timedelta(seconds=10)

# How many activities to keep in memory for the logbook and the history service.
MAX_EVENTS = 250

# Field names whose values must never reach the state machine, the recorder or a log line.
_SENSITIVE_FIELDS = ("pincode", "password", "secret", "token", "credential", "code")


def redact(value: Any, name: str = "") -> Any:
    """Recursively mask credential-looking values, keeping the shape."""
    if any(field in re.sub(r"[^a-z0-9]", "", name.lower()) for field in _SENSITIVE_FIELDS):
        return "**REDACTED**"
    if isinstance(value, dict):
        return {key: redact(item, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, name) for item in value]
    return value


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _states(features: dict[str, Any], feature: str) -> dict[str, Any]:
    return (features.get(feature) or {}).get("states") or {}


def _state(features: dict[str, Any], feature: str, key: str) -> dict[str, Any]:
    return _states(features, feature).get(key) or {}


def _entry_key(entry: dict[str, Any]) -> str:
    return "|".join(
        [
            str(entry.get("deviceIdFeatureState")),
            str(entry.get("lastUpdated")),
            json.dumps(entry.get("value"), sort_keys=True, default=str),
        ]
    )


def _parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _split_feature_state(feature_state: str) -> tuple[str, str]:
    """'report_event' -> ('report', 'event'), 'lock_state' -> ('lock', 'state')."""
    feature, _, key = feature_state.partition("_")
    return feature, key


class NimlyCloudCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds the account, its locations, users, devices and activity feed."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: Any,
        api: NimlyCloudApi,
        location_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=int(
                    (entry.options or {}).get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                )
            ),
        )
        self.entry = entry
        self.api = api
        self.location_id = location_id

        self.me: dict[str, Any] = {}
        self.locations: list[dict[str, Any]] = []
        self.location: dict[str, Any] = {}
        self.users: list[dict[str, Any]] = []
        self.guest_users: list[dict[str, Any]] = []
        self.home: dict[str, Any] = {}
        self.devices: list[dict[str, Any]] = []

        self.device_meta: dict[str, dict[str, Any]] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.access: dict[str, list[dict[str, Any]]] = {}
        self.events: list[dict[str, Any]] = []
        self.last_event: dict[str, Any] | None = None
        self.last_events: dict[str, dict[str, Any]] = {}
        # The most recent activity the cloud attributed to a person, per device. Kept
        # apart from last_events: an unattributed activity (auto-lock, a mirrored
        # action) must not erase who last opened the door.
        self.last_persons: dict[str, dict[str, Any]] = {}
        self.clock_skew_seconds: float | None = None
        # The feed's clock offset, derived from its server-computed expires
        # fields: the gateway stamps events DST-unaware (an hour ahead through
        # the summer) while the server's own arithmetic stays true.
        self._feed_offset_seconds: float = 0.0

        self._users_by_id: dict[str, dict[str, Any]] = {}
        self._seen_entries: set[str] = set()
        self._baseline_done = False

    # -- polling ------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            if not self.me:
                self.me = await self.api.async_me()
            if not self.locations:
                self.locations = await self.api.async_locations()
                self.location = next(
                    (
                        item
                        for item in self.locations
                        if item.get("locationId") == self.location_id
                    ),
                    {},
                )
            if not self.users:
                self.users = await self.api.async_location_users(self.location_id)
                self._users_by_id = {
                    str(user.get("id")): user for user in self.users if user.get("id")
                }
            # The app's "Guest user list": identities without an account.
            self.guest_users = await self.api.async_guest_users(self.location_id)

            home = await self.api.async_home(self.location_id)
            if isinstance(home, dict):
                self.home = home
            devices = home.get("devices", []) if isinstance(home, dict) else []

            # Rebuild from scratch: a device removed from the account must stop reporting
            # its last known values as if they were current.
            self.states = {}
            self.device_meta = {}
            self.access = {}

            for device in devices:
                device_id = device.get("id")
                if not device_id:
                    continue
                await self._poll_device(device, device_id)

        except NimlyCloudAuthError as err:
            # The user has to sign in again; Home Assistant shows the reauth flow
            # instead of retrying with credentials that will keep failing.
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except NimlyCloudError as err:
            raise UpdateFailed(str(err)) from err

        self.devices = devices
        return {
            "me": self.me,
            "location": self.location,
            "users": self.users,
            "home": self.home,
            "devices": self.devices,
            "states": self.states,
            "access": self.access,
            "events": self.events[:50],
        }

    async def _poll_device(self, device: dict[str, Any], device_id: str) -> None:
        """Refresh one device. A single failing device must not fail the whole poll."""
        try:
            state = await self.api.async_device(device_id)
        except NimlyCloudError as err:
            _LOGGER.debug("Could not read device %s: %s", device_id, err)
            return

        features = state.get("features") or {} if isinstance(state, dict) else {}
        self.states[device_id] = features
        self.device_meta[device_id] = (
            {**device, **state} if isinstance(state, dict) else dict(device)
        )
        try:
            self.access[device_id] = await self.api.async_device_access(device_id)
        except NimlyCloudError as err:
            _LOGGER.debug("Could not read access for %s: %s", device_id, err)

        try:
            history = await self.api.async_device_history(device_id)
        except NimlyCloudError as err:
            _LOGGER.debug("Could not read history for %s: %s", device_id, err)
            return

        if isinstance(history, list):
            self._feed_offset_seconds = self._feed_offset(history)
            self._ingest_history(device, history)

    # -- activity -----------------------------------------------------------

    def _ingest_history(self, device: dict[str, Any], entries: list[dict[str, Any]]) -> None:
        device_id = device.get("id")
        if not device_id or not entries:
            return

        fresh = []
        for entry in entries:
            key = _entry_key(entry)
            if key in self._seen_entries:
                continue
            self._seen_entries.add(key)
            fresh.append(entry)

        if not self._baseline_done:
            # The first feed already contains history. Keep it as history instead of
            # firing every past action as if it had just happened, and remember the
            # most recent activity the cloud attributed to a person.
            self._baseline_done = True
            self.events = self._collect(entries, self.events)
            for entry in sorted(
                entries, key=lambda item: str(item.get("lastUpdated") or "")
            ):
                self._remember_person(device_id, entry)
            return

        for entry in sorted(fresh, key=lambda item: str(item.get("lastUpdated") or "")):
            feature, key = _split_feature_state(str(entry.get("featureState") or ""))
            if feature == "report" and key == "event":
                event = self._build_activity(device, entry, entries)
                if event is None:
                    continue
                self.last_event = event
                self.last_events[device_id] = event
                self.events = self._collect([entry], self.events)
                if event.get("user_name"):
                    self.last_persons[device_id] = event
                _LOGGER.debug("Cloud activity: %s", event)
                self.hass.bus.async_fire(EVENT_NIMLY_CLOUD_ACTIVITY, event)
            else:
                self.events = self._collect([entry], self.events)
                self._remember_person(device_id, entry)

    def _remember_person(self, device_id: str, entry: dict[str, Any]) -> None:
        """Remember an entry the cloud attributed to a person.

        A feed entry carries userId/userName only when the vendor attributed it; the
        name lives next to the event (a lock_state entry) and is paired by
        _build_activity, so a built activity is stored whole while a raw entry only
        contributes its identity fields.
        """
        name = entry.get("userName") or entry.get("user_name")
        user_id = entry.get("userId") or entry.get("user_id")
        if not name and not user_id:
            return
        current = self.last_persons.get(device_id)
        if current and not current.get("user_name") and not name:
            return
        raw = (
            entry.get("time")
            or entry.get("lastUpdated")
            or entry.get("vendor_time")
        )
        self.last_persons[device_id] = {
            "device_id": device_id,
            "user_id": user_id,
            "user_name": name,
            "action": entry.get("action"),
            "source": entry.get("source"),
            "vendor_time": self.corrected_stamp(raw),
            "vendor_time_raw": raw,
            "observed_at": entry.get("observed_at"),
        }

    def corrected_stamp(self, stamp: Any) -> str | None:
        """A feed timestamp with the DST-unaware offset applied.

        The gateway stamps its own reports an hour ahead through the summer
        (the offset is read from the server's expires arithmetic); every sensor
        that shows a feed time goes through here so the dashboard and the app
        cannot disagree. The raw value stays available as `vendor_time_raw`.
        """
        if not stamp:
            return None
        parsed = _parse_stamp(stamp)
        if parsed is None:
            return str(stamp)
        if abs(self._feed_offset_seconds) > 60:
            parsed = parsed + timedelta(seconds=self._feed_offset_seconds)
        return parsed.isoformat()

    def _feed_offset(self, entries: list[dict[str, Any]]) -> float:
        """The feed clock's offset, from a server-computed expires field.

        State entries carry `expires`, which the vendor server computes with a
        true clock (lastUpdated + 30 days), while `lastUpdated` itself comes
        from the event source's DST-unaware clock. The difference is the offset
        to apply to every stamp; a candidate outside a sane range is ignored
        rather than guessed at.
        """
        for entry in entries:
            parsed = _parse_stamp(entry.get("lastUpdated"))
            expires = _parse_stamp(entry.get("expires"))
            if parsed is None or expires is None:
                continue
            candidate = (expires - timedelta(days=30) - parsed).total_seconds()
            if abs(candidate) <= 7200:
                return candidate
        return 0.0

    def _build_activity(
        self,
        device: dict[str, Any],
        entry: dict[str, Any],
        siblings: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        raw = entry.get("value")
        if not raw:
            return None

        action, source = parse_event(str(raw))
        if str(raw) not in CONFIRMED_EVENTS:
            _LOGGER.debug("Cloud event not yet confirmed: %s -> %s/%s", raw, action, source)

        user_id, user_name = self._attribute(entry, siblings)
        stamp = entry.get("lastUpdated")
        observed = datetime.now().astimezone().isoformat(timespec="seconds")
        corrected = stamp

        parsed = _parse_stamp(stamp)
        if parsed is not None:
            skew = (datetime.now(parsed.tzinfo) - parsed).total_seconds()
            if abs(skew) < 86400:
                self.clock_skew_seconds = skew
            if abs(self._feed_offset_seconds) > 60:
                # The feed's DST-unaware clock: shift the stamp by the offset the
                # server's own expires fields revealed, so ordering, the journal
                # and the app-visible sensors agree with real time.
                corrected = (
                    (parsed + timedelta(seconds=self._feed_offset_seconds))
                    .astimezone(parsed.tzinfo)
                    .isoformat()
                )

        return {
            "device_id": device.get("id"),
            "device": device.get("name"),
            # The module's IEEE address, so a local integration can match this activity
            # to the same lock without knowing the vendor's device identifier.
            "serial": self.device_serial(device.get("id")),
            "raw": raw,
            "action": action,
            "source": source,
            # The vendor feed does not expose the credential slot; a consumer that knows
            # the lock's local slots may fill this in itself.
            "slot": None,
            "user_id": user_id,
            "user_name": user_name,
            "time": corrected,  # corrected for the feed's DST-unaware clock
            "vendor_time": corrected,
            "vendor_time_raw": stamp,  # exactly as the feed sent it, for diagnostics
            "observed_at": observed,
            "feature_state": entry.get("featureState"),
        }

    def _attribute(
        self, entry: dict[str, Any], siblings: list[dict[str, Any]]
    ) -> tuple[str | None, str | None]:
        """Find the person the cloud attributed an action to.

        The name lives on the adjacent `lock_state` entry rather than on the event itself,
        so the closest one in time wins.
        """
        if entry.get("userName") or entry.get("userId"):
            return entry.get("userId"), entry.get("userName")

        stamp = _parse_stamp(entry.get("lastUpdated"))
        if stamp is None:
            return None, None

        best: dict[str, Any] | None = None
        best_distance: float | None = None
        for candidate in siblings:
            if candidate.get("featureState") != HISTORY_LOCK_STATE:
                continue
            if not (candidate.get("userName") or candidate.get("userId")):
                continue
            candidate_stamp = _parse_stamp(candidate.get("lastUpdated"))
            if candidate_stamp is None:
                continue
            distance = abs((candidate_stamp - stamp).total_seconds())
            if best_distance is None or distance < best_distance:
                best, best_distance = candidate, distance

        if best is None or best_distance is None:
            return None, None
        if best_distance > ATTRIBUTION_WINDOW.total_seconds():
            return None, None
        return best.get("userId"), best.get("userName")

    def _collect(
        self, new_entries: list[dict[str, Any]], existing: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        combined = existing + [self._as_event(entry) for entry in new_entries]
        combined.sort(key=lambda item: str(item.get("vendor_time") or ""), reverse=True)
        return combined[:MAX_EVENTS]

    def _as_event(self, entry: dict[str, Any]) -> dict[str, Any]:
        """A raw feed entry, shaped like an activity but without interpretation.

        The feed clock's offset is applied here, so every consumer - the event
        list, the last-event sensor, fetch_history - sees real time. The stamp
        exactly as the feed sent it stays in vendor_time_raw for diagnostics.
        """
        stamp = entry.get("lastUpdated")
        corrected = stamp
        parsed = _parse_stamp(stamp)
        if parsed is not None and abs(self._feed_offset_seconds) > 60:
            corrected = (
                (parsed + timedelta(seconds=self._feed_offset_seconds))
                .astimezone(parsed.tzinfo)
                .isoformat()
            )
        return {
            "device_id": entry.get("deviceId"),
            "raw": entry.get("value"),
            "vendor_time": corrected,
            "vendor_time_raw": stamp,
            "user_id": entry.get("userId"),
            "user_name": entry.get("userName"),
            "feature_state": entry.get("featureState"),
        }

    # -- device registry ----------------------------------------

    def device_serial(self, device_id: str | None) -> str | None:
        """The module's IEEE address (the vendor's serialNumber), normalised.

        Returned as 16 lowercase hex characters, the same shape Home Assistant uses
        for Zigbee identifiers, so a listener can match an activity to its lock.
        """
        if not device_id:
            return None
        serial = self.device_meta.get(device_id, {}).get("serialNumber")
        if not serial:
            return None
        normalised = str(serial).replace(":", "").replace("-", "").lower()
        return normalised if len(normalised) == 16 else None

    # -- helpers for entities ----------------------------------------------

    def device_state(self, device_id: str, feature: str, key: str) -> Any:
        return _state(self.states.get(device_id, {}), feature, key).get("value")

    def device_stamp(self, device_id: str, feature: str, key: str) -> Any:
        return _state(self.states.get(device_id, {}), feature, key).get("lastUpdated")

    def device_setting(self, device_id: str, key: str) -> Any:
        settings = self.device_meta.get(device_id, {}).get("settings") or {}
        return settings.get(key)

    def device_meta_value(self, device_id: str, key: str) -> Any:
        return self.device_meta.get(device_id, {}).get(key)

    def device_last_event(self, device_id: str) -> dict[str, Any]:
        return _state(self.states.get(device_id, {}), "report", "event")

    def device_events(self, device_id: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("device_id") == device_id]

    def device_access(self, device_id: str) -> list[dict[str, Any]]:
        return self.access.get(device_id, [])

    def user_by_id(self, user_id: str | None) -> dict[str, Any]:
        return self._users_by_id.get(str(user_id), {}) if user_id else {}

    def location_value(self, key: str) -> Any:
        return self.home.get(key)

    def gateway(self) -> dict[str, Any]:
        gateway = self.home.get("gateway")
        return gateway if isinstance(gateway, dict) else {}

    async def async_push_settings(
        self, device_id: str, settings: dict[str, Any]
    ) -> None:
        """Write the app's record and refresh, so HA state follows immediately.

        Convenience only: the local path never depends on it and callers treat
        a failure as a log line, not an error.
        """
        await self.api.async_update_device_settings(device_id, settings)
        await self.async_request_refresh()

    def device_raw(self, device_id: str) -> dict[str, Any]:
        """The device's whole cloud state, credentials masked. For diagnostics."""
        meta = {
            key: value
            for key, value in self.device_meta.get(device_id, {}).items()
            if key != "features"
        }
        return {
            "features": redact(self.states.get(device_id, {})),
            "device": redact(meta),
            "access": redact(self.access.get(device_id, [])),
        }

    def account_raw(self) -> dict[str, Any]:
        return {
            "me": redact(self.me),
            "location": redact(self.location),
            "gateway": redact(self.gateway()),
            "users": redact(self.users),
            "remainingPinAttempts": self.location_value("remainingPinAttempts"),
            "alarmState": self.location_value("alarmState"),
        }
