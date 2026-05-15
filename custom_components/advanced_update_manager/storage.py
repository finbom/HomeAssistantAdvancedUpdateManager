"""Persistent storage for release dates — survives HA restarts."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION


class UpdateDateStorage:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._dates: dict[str, str] = {}  # "entity_id|version" -> "YYYY-MM-DD"

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self._dates = data.get("dates", {})

    def get(self, entity_id: str, version: str) -> str | None:
        return self._dates.get(f"{entity_id}|{version}")

    async def async_set(self, entity_id: str, version: str, date: str) -> None:
        self._dates[f"{entity_id}|{version}"] = date
        await self._store.async_save({"dates": self._dates})
