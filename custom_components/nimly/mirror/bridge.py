"""The bridge entry: the bridge as its own device (online, firmware, OTA).

Created when a bridge is provisioned through Bluetooth discovery, so the discovery
card does not come back and the bridge appears as a real device in Home Assistant.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from ..const import (
    CONF_ADDRESS,
    CONF_OTA_MANIFEST_URL,
    CONF_PREFIX,
    DEFAULT_OTA_MANIFEST_URL,
    DEFAULT_PREFIX,
    DOMAIN,
    MANIFEST_REFRESH,
    TOPIC_BRIDGE_INFO,
    TOPIC_HA_TO_BRIDGE,
    TOPIC_OTA,
)
from .discovery import LEGACY_TOPIC, WILDCARD_TOPIC, mac_match

_LOGGER = logging.getLogger(__name__)

ONLINE_TIMEOUT = 180  # seconds without a sign of life -> offline


class BridgeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds the bridge info (`nimly/info`), OTA status and presence."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_bridge",
            # A short tick so presence can go offline quickly; the manifest is
            # fetched on its own, slower schedule.
            update_interval=timedelta(seconds=30),
        )
        self.entry = entry
        self.address: str | None = entry.data.get(CONF_ADDRESS)
        self.prefix: str | None = (
            entry.options.get(CONF_PREFIX) or entry.data.get(CONF_PREFIX) or None
        )
        self.info: dict[str, Any] = {}
        self.ota: dict[str, Any] = {}
        self.manifest: dict[str, Any] | None = None
        self.online = False
        self._last_seen = 0.0
        self._manifest_at = 0.0
        self._unsubs: list = []
        self.ota_manifest_url = (
            entry.options.get(CONF_OTA_MANIFEST_URL) or DEFAULT_OTA_MANIFEST_URL
        )

    async def async_setup(self) -> None:
        # Identity discovery: every kit announces on its own <prefix>/info, so a
        # wildcard finds them all; the legacy shared topic covers firmware 0.5.x.
        for topic in (WILDCARD_TOPIC, LEGACY_TOPIC):
            self._unsubs.append(
                await mqtt.async_subscribe(self.hass, topic, self._on_info, qos=1)
            )
        if self.prefix:
            await self._subscribe_prefix_topics()
        await self._async_fetch_manifest()
        self._publish()

    async def _subscribe_prefix_topics(self) -> None:
        """The kit's own prefix topics: OTA status and presence signs."""
        if not self.prefix:
            return
        self._unsubs.append(
            await mqtt.async_subscribe(
                self.hass, f"{self.prefix}/{TOPIC_OTA}", self._on_ota, qos=1
            )
        )
        for suffix in ("state", "battery"):
            self._unsubs.append(
                await mqtt.async_subscribe(
                    self.hass, f"{self.prefix}/{suffix}", self._on_seen, qos=1
                )
            )

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _on_info(self, msg: mqtt.ReceiveMessage) -> None:
        """A kit's retained identity (any prefix): adopt it when it is our MAC."""
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        if msg.topic == LEGACY_TOPIC and self.info:
            # Bootstrap only: the live per-prefix identity is authoritative.
            return
        if not mac_match(data.get("bridge"), self.address):
            return
        prefix = data.get("prefix")
        if isinstance(prefix, str) and prefix and prefix.rstrip("/") != (self.prefix or ""):
            self.prefix = prefix.rstrip("/")
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, CONF_PREFIX: self.prefix}
            )
            self.hass.async_create_task(self._subscribe_prefix_topics())
        self.info = data
        self._last_seen = time.monotonic()
        self.online = True
        self._publish()

    @callback
    def _on_ota(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if isinstance(data, dict):
            self.ota = data
            self._last_seen = time.monotonic()
            self._publish()

    @callback
    def _on_seen(self, msg: mqtt.ReceiveMessage) -> None:
        self._last_seen = time.monotonic()
        if not self.online:
            self.online = True
            self._publish()

    async def _async_fetch_manifest(self) -> None:
        self._manifest_at = time.monotonic()
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(self.ota_manifest_url, timeout=20) as resp:
                if resp.status == 200:
                    self.manifest = await resp.json(content_type=None)
        except (TimeoutError, ValueError, aiohttp.ClientError) as err:
            _LOGGER.debug("Could not fetch the OTA manifest: %s", err)

    async def async_ota(self, url: str) -> None:
        prefix = self.prefix or DEFAULT_PREFIX
        await mqtt.async_publish(
            self.hass,
            f"{prefix}/{TOPIC_HA_TO_BRIDGE}",
            json.dumps({"cmd": "ota", "url": url}, separators=(",", ":")),
            qos=1,
            retain=False,
        )

    def binary_url(self) -> str | None:
        manifest = self.manifest or {}
        builds = manifest.get("builds") or {}
        target = self.info.get("target")
        name = builds.get(target) if target else None
        if not isinstance(name, str) and len(builds) == 1:
            # An older bridge firmware may not announce its target; one
            # published build is unambiguous.
            name = next(iter(builds.values()))
        if not isinstance(name, str):
            return None
        return f"{self.ota_manifest_url.rsplit('/', 1)[0]}/{name}"

    def _snapshot(self) -> dict[str, Any]:
        return {
            "info": dict(self.info),
            "ota": dict(self.ota),
            "manifest": self.manifest,
            "online": self.online
            and (time.monotonic() - self._last_seen) < ONLINE_TIMEOUT,
        }

    @callback
    def _publish(self) -> None:
        self.async_set_updated_data(self._snapshot())

    async def _async_update_data(self) -> dict[str, Any]:
        if time.monotonic() - self._manifest_at >= MANIFEST_REFRESH:
            await self._async_fetch_manifest()
        return self._snapshot()


class BridgeEntity(CoordinatorEntity[BridgeCoordinator]):
    """Base for the bridge entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BridgeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"bridge_{coordinator.address}")},
            name="Nimly Bridge",
            manufacturer="nimly",
            model=coordinator.info.get("model", "Nimly Bridge"),
            configuration_url="https://github.com/c14ym0re/nimly",
        )
