"""Config and options flow for nimly.

The user step is a menu: today the vendor account (``cloud``); the lock mirror (``mirror``)
and the bridge provisioning (``bridge``) arrive with the merge's next step.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .cloud.api import NimlyCloudApi, NimlyCloudAuthError, NimlyCloudError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_COMPANY_ID,
    CONF_EMAIL,
    CONF_LOCATION_ID,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_TYPE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    TYPE_CLOUD,
)

_LOGGER = logging.getLogger(__name__)


class NimlyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up a Nimly entry: the cloud account first, mirror and bridge next."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str | None = None
        self._api: NimlyCloudApi | None = None
        self._locations: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=[TYPE_CLOUD])

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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return NimlyOptionsFlow()


class NimlyOptionsFlow(config_entries.OptionsFlow):
    """Options for the cloud entry: how often to ask the account for new activity."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
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
