"""Binary sensor platform for Advanced Update Manager — Autopilot running."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_AUTOPILOT_UPDATED, SIGNAL_FEELING_LUCKY_UPDATED


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([AutopilotRunningBinarySensor(entry.entry_id)])


class AutopilotRunningBinarySensor(BinarySensorEntity):
    """On from the moment a run starts (backup included) until it ends —
    handy as a condition to hold off other automations during a run."""

    _attr_has_entity_name = True
    _attr_name = "Autopilot Running"
    _attr_icon = "mdi:airplane-clock"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_should_poll = False

    def __init__(self, entry_id: str) -> None:
        self._attr_unique_id = f"{entry_id}_autopilot_running"
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
    def is_on(self) -> bool:
        return self.hass.data[DOMAIN]["autopilot"].is_running

    async def async_added_to_hass(self) -> None:
        for signal in (SIGNAL_AUTOPILOT_UPDATED, SIGNAL_FEELING_LUCKY_UPDATED):
            self.async_on_remove(async_dispatcher_connect(self.hass, signal, self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
