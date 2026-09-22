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
    CONF_OTA_MANIFEST_URL,
    CONF_PASSWORD,
    CONF_PREFIX,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_TYPE,
    DEFAULT_CHANNELS,
    DEFAULT_OTA_MANIFEST_URL,
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
from .mirror.pin_rules import (  # noqa: E402 - after the const imports
    OPTION_RESERVED_SLOTS,
    RESERVED_SLOTS_MAX,
    RESERVED_SLOTS_MIN,
    first_user_slot,
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

    # --- Reauthentication ------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """The cloud session died; ask for the password again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            api = NimlyCloudApi(self.hass)
            try:
                await api.async_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
                locations = await api.async_locations()
            except NimlyCloudAuthError:
                errors["base"] = "invalid_auth"
            except NimlyCloudError:
                errors["base"] = "cannot_connect"
            else:
                known = entry.data.get(CONF_LOCATION_ID)
                if known and not any(
                    item.get("locationId") == known for item in locations
                ):
                    errors["base"] = "no_locations"
                else:
                    data = {
                        **entry.data,
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_ACCESS_TOKEN: api.access_token,
                        CONF_REFRESH_TOKEN: api.refresh_token,
                    }
                    if api.company_id:
                        data[CONF_COMPANY_ID] = api.company_id
                    self.hass.config_entries.async_update_entry(entry, data=data)
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EMAIL, default=entry.data.get(CONF_EMAIL)
                    ): selector.TextSelector(),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
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
    """Options: polling for the cloud entry; channels and slots for the mirror."""

    _pin_task: asyncio.Task | None = None
    _pin_input: dict[str, Any] | None = None
    _pin_error: str | None = None
    _clear_task: asyncio.Task | None = None
    _clear_input: dict[str, Any] | None = None
    _clear_error: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if self.config_entry.data.get(CONF_TYPE) == TYPE_MIRROR:
            return await self.async_step_mirror_menu()

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

    # -- mirror: menu -------------------------------------------------------

    async def async_step_mirror_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="mirror_menu",
            menu_options=[
                "slots",
                "slot_set_pin",
                "slot_name",
                "slot_clear",
                "channels",
                "firmware",
                "reserved",
            ],
        )

    def _mirror(self) -> Any:
        return self.hass.data[DOMAIN][self.config_entry.entry_id]

    async def async_step_firmware(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            url = str(user_input.get(CONF_OTA_MANIFEST_URL) or "").strip()
            options = dict(self.config_entry.options)
            if url:
                options[CONF_OTA_MANIFEST_URL] = url
            else:
                options.pop(CONF_OTA_MANIFEST_URL, None)
            return self.async_create_entry(data=options)
        current = self.config_entry.options.get(
            CONF_OTA_MANIFEST_URL, DEFAULT_OTA_MANIFEST_URL
        )
        return self.async_show_form(
            step_id="firmware",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_OTA_MANIFEST_URL, default=current
                    ): selector.TextSelector()
                }
            ),
        )

    # -- mirror: channels ---------------------------------------------------

    async def async_step_reserved(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """How many slots the lock keeps for master credentials."""
        if user_input is not None:
            options = dict(self.config_entry.options)
            options[OPTION_RESERVED_SLOTS] = int(user_input[OPTION_RESERVED_SLOTS])
            return self.async_create_entry(data=options)
        current = first_user_slot(self.config_entry.options)
        return self.async_show_form(
            step_id="reserved",
            data_schema=vol.Schema(
                {
                    vol.Required(OPTION_RESERVED_SLOTS, default=current): vol.All(
                        int,
                        vol.Range(min=RESERVED_SLOTS_MIN, max=RESERVED_SLOTS_MAX),
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

    async def async_step_channels(
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
        return self.async_show_form(step_id="channels", data_schema=_preset_schema())

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

    # -- mirror: slots ------------------------------------------------------

    def _slot_rows(self) -> list[str]:
        coordinator = self._mirror()
        rows = []
        for slot, data in coordinator.slots.items():
            name = str(data.get("name") or "unnamed")
            creds = ", ".join(coordinator.slots.credentials(slot)) or "no credential seen"
            rows.append(f"Slot {slot}: **{name}** - {creds}")
        return rows

    def _slot_options(self, *, only_occupied: bool = False) -> dict[str, str]:
        coordinator = self._mirror()
        options: dict[str, str] = {}
        for slot, data in coordinator.slots.items():
            if only_occupied and not coordinator.slots.occupied(slot):
                continue
            name = str(data.get("name") or "unnamed")
            creds = ", ".join(coordinator.slots.credentials(slot)) or "no credentials seen"
            options[str(slot)] = f"Slot {slot}: {name} ({creds})"
        return options

    async def async_step_slots(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_mirror_menu()
        rows = self._slot_rows() or ["No slots known yet."]
        return self.async_show_form(
            step_id="slots",
            data_schema=vol.Schema({}),
            description_placeholders={"slot_status": "\n".join(rows)},
        )

    async def async_step_slot_name(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            await self._mirror().async_set_slot_name(
                int(user_input["slot"]), str(user_input["name"])
            )
            return self.async_create_entry(data=self.config_entry.options)
        return self.async_show_form(
            step_id="slot_name",
            data_schema=vol.Schema(
                {
                    vol.Required("slot"): vol.Coerce(int),
                    vol.Required("name"): selector.TextSelector(),
                }
            ),
        )

    async def async_step_slot_set_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        suggested: dict[str, Any] | None = None
        if self._pin_error:
            errors["base"] = self._pin_error
            suggested = self._pin_input
            self._pin_error = None
        elif user_input is not None:
            code = str(user_input["code"])
            if not code.isdigit() or not 4 <= len(code) <= 8:
                errors["code"] = "invalid_pin"
                suggested = user_input
            else:
                self._pin_input = user_input
                return await self.async_step_slot_set_pin_progress()

        schema = vol.Schema(
            {
                vol.Required("slot"): vol.In(self._slot_options()),
                vol.Required("name"): selector.TextSelector(),
                vol.Required("code"): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        if suggested:
            schema = self.add_suggested_values_to_schema(schema, suggested)
        return self.async_show_form(
            step_id="slot_set_pin", data_schema=schema, errors=errors
        )

    async def async_step_slot_set_pin_progress(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if self._pin_task is None:
            self._pin_task = self.hass.async_create_task(self._do_set_pin())
        return self.async_show_progress(
            step_id="slot_set_pin_progress",
            progress_action="slot_set_pin",
            progress_task=self._pin_task,
        )

    async def async_step_slot_set_pin_progress_done(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        task = self._pin_task
        self._pin_task = None
        try:
            if task is not None:
                task.result()
        except Exception:  # noqa: BLE001 - surface the failure in the form
            _LOGGER.exception("Writing a PIN failed")
            self._pin_error = "lock_unreachable"
        else:
            self._pin_input = None
            return self.async_create_entry(data=self.config_entry.options)
        return self.async_show_progress_done(next_step_id="slot_set_pin")

    async def _do_set_pin(self) -> None:
        inp = self._pin_input
        assert inp is not None
        coordinator = self._mirror()
        slot = int(inp["slot"])
        await coordinator.async_set_slot_pin(slot, str(inp["code"]))
        if inp.get("name"):
            await coordinator.async_set_slot_name(slot, str(inp["name"]))

    async def async_step_slot_clear(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        options = self._slot_options(only_occupied=True)
        if not options:
            return self.async_abort(reason="no_active_slots")
        errors: dict[str, str] = {}
        if self._clear_error:
            errors["base"] = self._clear_error
            self._clear_error = None
        if user_input is not None and "slot" in user_input:
            self._clear_input = user_input
            return await self.async_step_slot_clear_progress()
        return self.async_show_form(
            step_id="slot_clear",
            data_schema=vol.Schema({vol.Required("slot"): vol.In(options)}),
            errors=errors,
        )

    async def async_step_slot_clear_progress(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if self._clear_task is None:
            self._clear_task = self.hass.async_create_task(self._do_clear())
        return self.async_show_progress(
            step_id="slot_clear_progress",
            progress_action="slot_clear",
            progress_task=self._clear_task,
        )

    async def async_step_slot_clear_progress_done(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        task = self._clear_task
        self._clear_task = None
        try:
            if task is not None:
                task.result()
        except Exception:  # noqa: BLE001 - surface the failure in the form
            _LOGGER.exception("Clearing the slot failed")
            self._clear_error = "lock_unreachable"
        else:
            self._clear_input = None
            return self.async_create_entry(data=self.config_entry.options)
        return self.async_show_progress_done(next_step_id="slot_clear")

    async def _do_clear(self) -> None:
        inp = self._clear_input
        assert inp is not None
        await self._mirror().async_clear_slot(int(inp["slot"]))
