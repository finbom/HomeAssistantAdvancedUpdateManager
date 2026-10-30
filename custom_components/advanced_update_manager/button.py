"""Button platform for Advanced Update Manager — Reset OPCON.

The only way OPCON goes from Code Red back to ok (no implicit reset via
starting a new run — see feeling_lucky.py). Lands in its own "Controls"
block on the device page, HA's normal behavior for button entities.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .feeling_lucky import FeelingLuckyRunner


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ResetOpconButton(entry.entry_id)])


class ResetOpconButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Reset OPCON (Clear Code Red)"
    _attr_icon = "mdi:restart-alert"

    def __init__(self, entry_id: str) -> None:
        self._attr_unique_id = f"{entry_id}_reset_opcon"
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Advanced Update Manager",
            manufacturer="Magnus Finbom",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        runner: FeelingLuckyRunner = self.hass.data[DOMAIN]["feeling_lucky_runner"]
        await runner.async_reset_opcon()
