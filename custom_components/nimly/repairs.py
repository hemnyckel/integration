"""Repair flow: name a slot that was used on the lock.

The issue carries both the slot and the config entry, so a household with more
than one mirrored lock names the slot on the right one.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import DOMAIN


class NewSlotRepairFlow(RepairsFlow):
    """Asks for the user's name and stores it in the slot table."""

    def __init__(self, slot: int, entry_id: str | None = None) -> None:
        self._slot = slot
        self._entry_id = entry_id

    def _mirror(self) -> Any:
        """The coordinator this issue belongs to, with a single-mirror fallback."""
        coordinators = self.hass.data.get(DOMAIN, {})
        if self._entry_id and self._entry_id in coordinators:
            return coordinators[self._entry_id]
        for coordinator in coordinators.values():
            if getattr(coordinator, "async_set_slot_name", None) is not None:
                return coordinator
        return None

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> RepairsFlowResult:
        # The first call carries the flow context, not None, so only a dict
        # with the actual field counts as a submission.
        coordinator = self._mirror()
        if user_input is not None and "name" in user_input:
            if coordinator is not None:
                await coordinator.async_set_slot_name(
                    self._slot, str(user_input["name"])
                )
            return self.async_create_entry(data={})

        # A name from the journal is offered as the default when exactly one
        # fits, but the user still confirms or replaces it.
        suggestion = (
            coordinator.suggest_slot_name(self._slot) if coordinator is not None else None
        )
        if suggestion:
            name_field: vol.Marker = vol.Required("name", default=suggestion)
        else:
            name_field = vol.Required("name")
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({name_field: selector.TextSelector()}),
            description_placeholders={"slot": str(self._slot)},
        )


class EmulatorNotJoinedRepairFlow(RepairsFlow):
    """Guided recovery when the emulator cannot join the bridge's network.

    The bridge accepts a join only when no stale device record blocks it:
    remove the old lock in the Nimly app, then start the app's add-device
    flow. The emulator steers on its own and the repair closes itself once it
    is back on the network.
    """

    def __init__(self, entry_id: str | None = None) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="init", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    """Dispatch on the issue; the data carries the slot or the entry id."""
    if issue_id.startswith("emulator_not_joined"):
        entry_id = data.get("entry_id") if isinstance(data, dict) else None
        return EmulatorNotJoinedRepairFlow(entry_id if isinstance(entry_id, str) else None)
    slot = data.get("slot") if isinstance(data, dict) else None
    if not isinstance(slot, int):
        try:
            # Ids since 1.0 look like "new_slot_12_ab12cd34".
            slot = int(issue_id.split("_")[2])
        except (IndexError, ValueError):
            slot = 0
    entry_id = data.get("entry_id") if isinstance(data, dict) else None
    return NewSlotRepairFlow(slot, entry_id if isinstance(entry_id, str) else None)
