"""Config and options flow for hemnyckel.

The user picks the lock entity (a lock paired in ZHA) and is done; the options
then manage that lock's slots and its master-slot reservation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_LOCK_ENTITY,
    CONF_TYPE,
    DOMAIN,
    TYPE_MIRROR,
)
_LOGGER = logging.getLogger(__name__)


class HemnyckelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up a Hemnyckel entry: the lock to manage."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            lock_entity: str = user_input[CONF_LOCK_ENTITY]
            await self.async_set_unique_id(lock_entity.lower())
            self._abort_if_unique_id_configured()
            # The title is what the entry list shows and what the device name
            # falls back to, so it is the lock's human name ("Ytterdörren"),
            # not the Zigbee module's serial. A lock that has no state yet
            # still gets a recognisable title from its entity id.
            lock_state = self.hass.states.get(lock_entity)
            title = (lock_state.name if lock_state is not None else None) or (
                lock_entity.split(".")[-1]
            )
            return self.async_create_entry(
                title=title,
                data={
                    CONF_TYPE: TYPE_MIRROR,
                    CONF_LOCK_ENTITY: lock_entity,
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCK_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="lock")
                    )
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return HemnyckelOptionsFlow()


class HemnyckelOptionsFlow(config_entries.OptionsFlow):
    """Options for the lock: slots and the master-slot reservation."""

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

    # -- lock: menu ---------------------------------------------------------

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
            ],
        )

    def _mirror(self) -> Any:
        return self.hass.data[DOMAIN][self.config_entry.entry_id]

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
