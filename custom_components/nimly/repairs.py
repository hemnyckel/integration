"""Repair flow: name a new slot created in the Nimly app."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import DOMAIN


class NewSlotRepairFlow(RepairsFlow):
    """Asks for the user's name and writes it to the lock slot."""

    def __init__(self, slot: int) -> None:
        self._slot = slot

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if user_input is not None:
            name = user_input["name"]
            for coordinator in self.hass.data.get(DOMAIN, {}).values():
                setter = getattr(coordinator, "async_set_slot_name", None)
                if setter is not None:
                    await setter(self._slot, name)
                    break
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required("name"): selector.TextSelector()}
            ),
            description_placeholders={"slot": str(self._slot)},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    """issue_id is "new_slot_<slot>"."""
    try:
        slot = int(issue_id.rsplit("_", 1)[-1])
    except ValueError:
        slot = 0
    return NewSlotRepairFlow(slot)
