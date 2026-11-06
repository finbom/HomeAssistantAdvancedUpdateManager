"""Switch platform for Advanced Update Manager — Autopilot on/off."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_AUTOPILOT_UPDATED


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([AutopilotSwitch(entry.entry_id)])


class AutopilotSwitch(SwitchEntity):
    """Engages/disengages the Autopilot schedule (e.g. off while on vacation).
    Run Now still works while this is off."""

    _attr_has_entity_name = True
    _attr_name = "Autopilot"
    _attr_should_poll = False

    def __init__(self, entry_id: str) -> None:
        self._attr_unique_id = f"{entry_id}_autopilot"
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Advanced Update Manager",
            manufacturer="Magnus Finbom",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def icon(self) -> str:
        return "mdi:airplane" if self.is_on else "mdi:airplane-off"

    @property
    def is_on(self) -> bool:
        return bool(self.hass.data[DOMAIN]["autopilot"].config.get("enabled"))

    async def async_turn_on(self, **kwargs) -> None:
        await self.hass.data[DOMAIN]["autopilot"].async_set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.hass.data[DOMAIN]["autopilot"].async_set_enabled(False)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_AUTOPILOT_UPDATED, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
