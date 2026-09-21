"""Repair flow: name a slot that was used on the lock."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import DOMAIN


class NewSlotRepairFlow(RepairsFlow):
    """Asks for the user's name and stores it in the slot table."""

    def __init__(self, slot: int) -> None:
        self._slot = slot

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> RepairsFlowResult:
        # The first call carries the flow context, not None, so only a dict
        # with the actual field counts as a submission.
        if user_input is not None and "name" in user_input:
            name = str(user_input["name"])
            for coordinator in self.hass.data.get(DOMAIN, {}).values():
                setter = getattr(coordinator, "async_set_slot_name", None)
                if setter is not None:
                    await setter(self._slot, name)
                    break
            return self.async_create_entry(data={})

        # A cloud user name is offered as the default when exactly one fits,
        # but the user still confirms or replaces it.
        suggestion = None
        for coordinator in self.hass.data.get(DOMAIN, {}).values():
            suggest = getattr(coordinator, "suggest_slot_name", None)
            if suggest is not None:
                suggestion = suggest(self._slot)
                break
        if suggestion:
            name_field: vol.Marker = vol.Required("name", default=suggestion)
        else:
            name_field = vol.Required("name")
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({name_field: selector.TextSelector()}),
            description_placeholders={"slot": str(self._slot)},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    """The issue data carries the slot; fall back to parsing the issue id."""
    slot = data.get("slot") if isinstance(data, dict) else None
    if not isinstance(slot, int):
        try:
            slot = int(issue_id.rsplit("_", 1)[-1])
        except ValueError:
            slot = 0
    return NewSlotRepairFlow(slot)
