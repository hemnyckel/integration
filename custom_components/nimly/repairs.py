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

        # A cloud user name is offered as the default when exactly one fits,
        # but the user still confirms or replaces it.
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

    Almost always the vendor account still holds the old device record, which
    makes the cloud refuse the re-pairing. The first step removes it here (the
    same call the app's "remove device" makes); the second hands the phone part
    over with clear instructions, and the repair closes itself once the
    emulator is back on the network.
    """

    def __init__(self, entry_id: str | None = None) -> None:
        self._entry_id = entry_id

    def _mirror(self) -> Any:
        coordinators = self.hass.data.get(DOMAIN, {})
        if self._entry_id and self._entry_id in coordinators:
            return coordinators[self._entry_id]
        for coordinator in coordinators.values():
            if getattr(coordinator, "async_reset_app_registration", None) is not None:
                return coordinator
        return None

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            coordinator = self._mirror()
            if coordinator is None:
                return self.async_abort(reason="no_mirror")
            result = await coordinator.async_reset_app_registration()
            if not result.get("reset"):
                return self.async_abort(reason="reset_failed")
            return await self.async_step_next()
        return self.async_show_form(step_id="init", data_schema=vol.Schema({}))

    async def async_step_next(
        self, user_input: dict | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="next", data_schema=vol.Schema({}))


class CloudPushRepairFlow(RepairsFlow):
    """Re-send a guest edit the vendor cloud never confirmed.

    The local edit already landed on the lock and in Home Assistant; only the
    app's copy is stale. The retry sends the values we hold again.
    """

    def __init__(self, slot: int, entry_id: str | None = None) -> None:
        self._slot = slot
        self._entry_id = entry_id

    def _mirror(self) -> Any:
        """The coordinator this issue belongs to, with a single-mirror fallback."""
        coordinators = self.hass.data.get(DOMAIN, {})
        if self._entry_id and self._entry_id in coordinators:
            return coordinators[self._entry_id]
        for coordinator in coordinators.values():
            if getattr(coordinator, "async_retry_cloud_push", None) is not None:
                return coordinator
        return None

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            coordinator = self._mirror()
            if coordinator is None:
                return self.async_abort(reason="no_mirror")
            if not await coordinator.async_retry_cloud_push(self._slot):
                return self.async_abort(reason="retry_failed")
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="init", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    """Dispatch on the issue; the data carries the slot or the entry id."""
    if issue_id.startswith("emulator_not_joined"):
        entry_id = data.get("entry_id") if isinstance(data, dict) else None
        return EmulatorNotJoinedRepairFlow(entry_id if isinstance(entry_id, str) else None)
    if issue_id.startswith("cloud_push_"):
        slot = data.get("slot") if isinstance(data, dict) else None
        if not isinstance(slot, int):
            try:
                slot = int(issue_id.split("_")[2])
            except (IndexError, ValueError):
                slot = 0
        entry_id = data.get("entry_id") if isinstance(data, dict) else None
        return CloudPushRepairFlow(slot, entry_id if isinstance(entry_id, str) else None)
    slot = data.get("slot") if isinstance(data, dict) else None
    if not isinstance(slot, int):
        try:
            # Ids since 1.0 look like "new_slot_12_ab12cd34".
            slot = int(issue_id.split("_")[2])
        except (IndexError, ValueError):
            slot = 0
    entry_id = data.get("entry_id") if isinstance(data, dict) else None
    return NewSlotRepairFlow(slot, entry_id if isinstance(entry_id, str) else None)
