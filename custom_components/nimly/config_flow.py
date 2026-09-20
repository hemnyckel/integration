"""Config and options flow for nimly.

The user step is a menu: the vendor account (``cloud``) or the lock mirror (``mirror``).
The bridge (``bridge``) is discovered over Bluetooth and provisioned with Improv.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import mqtt
from homeassistant.core import callback
from homeassistant.helpers import selector

from .cloud.api import NimlyCloudApi, NimlyCloudAuthError, NimlyCloudError
from .const import (
    CHANNELS,
    CONF_ACCESS_TOKEN,
    CONF_ADDRESS,
    CONF_CHANNELS,
    CONF_COMPANY_ID,
    CONF_EMAIL,
    CONF_LOCK_ENTITY,
    CONF_LOCATION_ID,
    CONF_PASSWORD,
    CONF_PREFIX,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_TYPE,
    DEFAULT_CHANNELS,
    DEFAULT_PREFIX,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PRESET_CUSTOM,
    PRESET_FULL,
    PRESET_HA_ONLY,
    PRESET_NOTIFICATIONS,
    PRESETS,
    TOPIC_BRIDGE_INFO,
    TYPE_BRIDGE,
    TYPE_CLOUD,
    TYPE_MIRROR,
)

_LOGGER = logging.getLogger(__name__)

PRESET_CHOICES = [
    selector.SelectOptionDict(value=PRESET_FULL, label="Full mirror"),
    selector.SelectOptionDict(value=PRESET_NOTIFICATIONS, label="Notifications only"),
    selector.SelectOptionDict(value=PRESET_HA_ONLY, label="HA control only"),
    selector.SelectOptionDict(value=PRESET_CUSTOM, label="Custom"),
]


def _preset_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("preset", default=PRESET_FULL): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=PRESET_CHOICES,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


def _channels_schema(defaults: dict[str, bool]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                f"ch_{key}", default=defaults.get(key, DEFAULT_CHANNELS.get(key, False))
            ): bool
            for key in CHANNELS
        }
    )


def _extract_channels(user_input: dict[str, Any]) -> dict[str, bool]:
    return {key: bool(user_input.get(f"ch_{key}")) for key in CHANNELS}


async def _detect_bridge(hass: Any) -> dict[str, Any] | None:
    """Listens briefly to the bridge's retained nimly/info so the wizard can prefill the prefix."""
    fut: asyncio.Future = hass.loop.create_future()

    @callback
    def _cb(msg: mqtt.ReceiveMessage) -> None:
        if fut.done():
            return
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            fut.set_result(None)
            return
        fut.set_result(data if isinstance(data, dict) else None)

    unsub = await mqtt.async_subscribe(hass, TOPIC_BRIDGE_INFO, _cb, qos=1)
    try:
        return await asyncio.wait_for(fut, timeout=3)
    except asyncio.TimeoutError:
        return None
    finally:
        unsub()


class NimlyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up a Nimly entry: the vendor account, the lock mirror or the bridge."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str | None = None
        self._api: NimlyCloudApi | None = None
        self._locations: list[dict[str, Any]] = []
        self._lock_entity: str | None = None
        self._prefix: str = DEFAULT_PREFIX
        self._channels: dict[str, bool] = dict(DEFAULT_CHANNELS)
        self._ble_address: str | None = None
        self._ble_name: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="user", menu_options=[TYPE_CLOUD, TYPE_MIRROR]
        )

    # --- Cloud -----------------------------------------------------------------

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api = NimlyCloudApi(self.hass)
            try:
                await api.async_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
                self._locations = await api.async_locations()
            except NimlyCloudAuthError:
                errors["base"] = "invalid_auth"
            except NimlyCloudError:
                errors["base"] = "cannot_connect"
            else:
                if not self._locations:
                    errors["base"] = "no_locations"
                else:
                    self._email = user_input[CONF_EMAIL]
                    self._api = api
                    await self.async_set_unique_id((self._email or "").lower())
                    self._abort_if_unique_id_configured()
                    return await self.async_step_location()

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): selector.TextSelector(),
                vol.Required(CONF_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="cloud", data_schema=schema, errors=errors)

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            location_id = user_input[CONF_LOCATION_ID]
            location = next(
                (item for item in self._locations if item.get("locationId") == location_id),
                {},
            )
            assert self._api is not None
            data: dict[str, Any] = {
                CONF_TYPE: TYPE_CLOUD,
                CONF_EMAIL: self._email,
                CONF_LOCATION_ID: location_id,
                CONF_ACCESS_TOKEN: self._api.access_token,
                CONF_REFRESH_TOKEN: self._api.refresh_token,
            }
            if self._api.company_id:
                data[CONF_COMPANY_ID] = self._api.company_id
            return self.async_create_entry(
                title=f"Nimly Cloud ({location.get('name') or self._email})",
                data=data,
            )

        options = [
            selector.SelectOptionDict(
                value=str(item.get("locationId")),
                label=f"{item.get('name')} ({item.get('role', '')})".strip(),
            )
            for item in self._locations
            if item.get("locationId")
        ]
        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCATION_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    # --- Mirror ----------------------------------------------------------------

    async def async_step_mirror(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            lock_entity: str = user_input[CONF_LOCK_ENTITY]
            self._lock_entity = lock_entity
            self._prefix = user_input.get(CONF_PREFIX) or DEFAULT_PREFIX
            await self.async_set_unique_id(lock_entity.lower())
            self._abort_if_unique_id_configured()
            return await self.async_step_channels()

        detected = await _detect_bridge(self.hass)
        prefix_default = DEFAULT_PREFIX
        if detected:
            found = detected.get("prefix")
            if isinstance(found, str) and found:
                prefix_default = found
            text = (
                f"Found the bridge {detected.get('model', 'Nimly Bridge')} "
                f"({detected.get('bridge', '?')}), firmware {detected.get('fw', '?')}."
            )
        else:
            text = (
                "No bridge was found automatically (nimly/info is empty) - "
                "enter the MQTT prefix manually."
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCK_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="lock")
                ),
                vol.Optional(CONF_PREFIX, default=prefix_default): selector.TextSelector(
                    selector.TextSelectorConfig()
                ),
            }
        )
        return self.async_show_form(
            step_id="mirror",
            data_schema=schema,
            description_placeholders={"detected": text},
        )

    async def async_step_channels(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            preset = user_input.get("preset", PRESET_FULL)
            if preset == PRESET_CUSTOM:
                return self.async_show_form(
                    step_id="custom",
                    data_schema=_channels_schema(self._channels),
                )
            self._channels = dict(PRESETS.get(preset, DEFAULT_CHANNELS))
            return self._create_mirror()

        return self.async_show_form(
            step_id="channels",
            data_schema=_preset_schema(),
            description_placeholders={"lock": self._lock_entity or ""},
        )

    async def async_step_custom(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._channels = _extract_channels(user_input)
            return self._create_mirror()
        return self.async_show_form(
            step_id="custom", data_schema=_channels_schema(self._channels)
        )

    def _create_mirror(self) -> config_entries.ConfigFlowResult:
        assert self._lock_entity is not None
        return self.async_create_entry(
            title=f"Nimly Mirror ({self._lock_entity.split('.')[-1]})",
            data={
                CONF_TYPE: TYPE_MIRROR,
                CONF_LOCK_ENTITY: self._lock_entity,
                CONF_PREFIX: self._prefix,
            },
            options={CONF_CHANNELS: self._channels},
        )

    # --- Bridge ----------------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: Any
    ) -> config_entries.ConfigFlowResult:
        """The bridge was discovered over Bluetooth (the Improv advertisement)."""
        address = getattr(discovery_info, "address", None)
        if not address:
            return self.async_abort(reason="no_address")
        await self.async_set_unique_id(address, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        self._ble_address = address
        self._ble_name = getattr(discovery_info, "name", None) or address
        return await self.async_step_wifi()

    async def async_step_wifi(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Provide Wi-Fi for the bridge and provision over BLE."""
        errors: dict[str, str] = {}
        if user_input is not None and self._ble_address:
            from .mirror.improv_ble import async_provision

            try:
                await async_provision(
                    self.hass,
                    self._ble_address,
                    user_input["ssid"],
                    user_input.get("password") or "",
                )
            except Exception as err:  # noqa: BLE001 - surface the error in the form
                errors["base"] = "provision_failed"
                _LOGGER.warning("Improv provisioning failed: %s", err)
            else:
                return self.async_create_entry(
                    title=f"Nimly Bridge ({self._ble_name or 'bridge'})",
                    data={CONF_TYPE: TYPE_BRIDGE, CONF_ADDRESS: self._ble_address},
                )

        schema = vol.Schema(
            {
                vol.Required("ssid"): selector.TextSelector(),
                vol.Optional("password", default=""): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="wifi",
            data_schema=schema,
            errors=errors,
            description_placeholders={"device": self._ble_name or "the bridge"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return NimlyOptionsFlow()


class NimlyOptionsFlow(config_entries.OptionsFlow):
    """Options: polling for the cloud entry, channels for the mirror."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if self.config_entry.data.get(CONF_TYPE) == TYPE_MIRROR:
            return await self.async_step_mirror(user_input)

        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        int, vol.Range(min=10, max=600)
                    )
                }
            ),
        )

    def _current_channels(self) -> dict[str, bool]:
        entry = self.config_entry
        return dict(
            entry.options.get(CONF_CHANNELS)
            or entry.data.get(CONF_CHANNELS)
            or DEFAULT_CHANNELS
        )

    async def async_step_mirror(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            preset = user_input.get("preset", PRESET_CUSTOM)
            if preset == PRESET_CUSTOM:
                return self.async_show_form(
                    step_id="custom",
                    data_schema=_channels_schema(self._current_channels()),
                )
            return self.async_create_entry(
                data={CONF_CHANNELS: dict(PRESETS.get(preset, DEFAULT_CHANNELS))}
            )
        return self.async_show_form(step_id="mirror", data_schema=_preset_schema())

    async def async_step_custom(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_CHANNELS: _extract_channels(user_input)}
            )
        return self.async_show_form(
            step_id="custom", data_schema=_channels_schema(self._current_channels())
        )
