"""Config and options flow for nimly.

The user step picks the lock mirror (``mirror``); the bridge (``bridge``) is
discovered over Bluetooth and provisioned with Improv.
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

from .const import (
    CHANNELS,
    CONF_ADDRESS,
    CONF_BRIDGE,
    CONF_CHANNELS,
    CONF_LOCK_ENTITY,
    CONF_OTA_MANIFEST_URL,
    CONF_PREFIX,
    CONF_TYPE,
    DEFAULT_CHANNELS,
    DEFAULT_OTA_MANIFEST_URL,
    DEFAULT_PREFIX,
    DOMAIN,
    PRESET_CUSTOM,
    PRESET_FULL,
    PRESET_HA_ONLY,
    PRESET_NOTIFICATIONS,
    PRESETS,
    TOPIC_BRIDGE_INFO,
    TYPE_BRIDGE,
    TYPE_MIRROR,
)
from .mirror.pin_rules import (  # noqa: E402 - after the const imports
    OPTION_RESERVED_SLOTS,
    RESERVED_SLOTS_MAX,
    RESERVED_SLOTS_MIN,
    first_user_slot,
)
from .mirror.discovery import (  # noqa: E402 - after the const imports
    LEGACY_TOPIC,
    WILDCARD_TOPIC,
    collect_bridges,
    valid_prefix,
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


async def _detect_bridges(hass: Any) -> list[dict[str, Any]]:
    """Every kit's retained identity, for the wizard's bridge picker.

    Listens briefly to the wildcard nimly/+/info (firmware 0.6.0+, one topic per
    kit) plus the legacy shared nimly/info (0.5.x). Each kit is returned with a
    ``free`` flag: a prefix another mirror entry already bound is taken, so the
    wizard only offers kits nobody owns.
    """
    payloads: list[dict[str, Any]] = []

    @callback
    def _cb(msg: mqtt.ReceiveMessage) -> None:
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if isinstance(data, dict):
            payloads.append(data)

    unsubs = [
        await mqtt.async_subscribe(hass, topic, _cb, qos=1)
        for topic in (WILDCARD_TOPIC, LEGACY_TOPIC)
    ]
    try:
        await asyncio.sleep(3)
    finally:
        for unsub in unsubs:
            unsub()
    used = {
        entry.options.get(CONF_PREFIX) or entry.data.get(CONF_PREFIX) or DEFAULT_PREFIX
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_TYPE) == TYPE_MIRROR
    }
    return collect_bridges(payloads, used)


class NimlyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up a Nimly entry: the lock mirror or the bridge."""

    VERSION = 1

    def __init__(self) -> None:
        self._lock_entity: str | None = None
        self._prefix: str = DEFAULT_PREFIX
        self._bridge: str | None = None
        self._bridges: list[dict[str, Any]] = []
        self._channels: dict[str, bool] = dict(DEFAULT_CHANNELS)
        self._ble_address: str | None = None
        self._ble_name: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="user", menu_options=[TYPE_MIRROR]
        )

    # --- Mirror ----------------------------------------------------------------

    async def async_step_mirror(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            lock_entity: str = user_input[CONF_LOCK_ENTITY]
            chosen = str(user_input.get(CONF_BRIDGE) or "")
            bridge = next(
                (item for item in self._bridges if item["mac"] == chosen), None
            )
            prefix = (
                bridge["prefix"] if bridge else str(user_input.get(CONF_PREFIX) or "")
            ).rstrip("/") or DEFAULT_PREFIX
            if not valid_prefix(prefix):
                errors["base"] = "invalid_prefix"
            elif any(
                (
                    entry.options.get(CONF_PREFIX)
                    or entry.data.get(CONF_PREFIX)
                    or DEFAULT_PREFIX
                )
                == prefix
                for entry in self.hass.config_entries.async_entries(DOMAIN)
                if entry.data.get(CONF_TYPE) == TYPE_MIRROR
            ):
                errors["base"] = "prefix_in_use"
            if not errors:
                self._lock_entity = lock_entity
                self._prefix = prefix
                self._bridge = bridge["mac"] if bridge else None
                await self.async_set_unique_id(lock_entity.lower())
                self._abort_if_unique_id_configured()
                return await self.async_step_channels()

        if not self._bridges:
            self._bridges = await _detect_bridges(self.hass)
        free = [item for item in self._bridges if item["free"]]
        prefix_default = free[0]["prefix"] if free else DEFAULT_PREFIX
        if self._bridges:
            listed = ", ".join(
                f"{item['prefix']} ({item['mac']}, fw {item['fw'] or '?'})"
                + ("" if item["free"] else " - in use")
                for item in self._bridges
            )
            text = (
                f"Found {len(self._bridges)} bridge(s): {listed}. "
                "Pick one, or enter a prefix manually."
            )
        else:
            text = (
                "No bridge was found automatically (nimly/+/info is empty) - "
                "enter the MQTT prefix manually."
            )

        fields: dict[Any, Any] = {
            vol.Required(CONF_LOCK_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="lock")
            )
        }
        if self._bridges:
            fields[
                vol.Optional(
                    CONF_BRIDGE,
                    default=(free[0]["mac"] if free else self._bridges[0]["mac"]),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=item["mac"],
                            label=f"{item['prefix']} · fw {item['fw'] or '?'}"
                            + ("" if item["free"] else " (in use)"),
                        )
                        for item in self._bridges
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        fields[vol.Optional(CONF_PREFIX, default=prefix_default)] = (
            selector.TextSelector()
        )
        return self.async_show_form(
            step_id="mirror",
            data_schema=vol.Schema(fields),
            errors=errors,
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
                **({CONF_BRIDGE: self._bridge} if self._bridge else {}),
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
    """Options for the mirror: channels, slots and the firmware source."""

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
        return self.async_abort(reason="no_options")

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
