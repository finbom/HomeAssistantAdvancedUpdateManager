"""DataUpdateCoordinator — fetches and enriches all update.* entities."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CORE_ENTITY_ID,
    DOMAIN,
    HAOS_ENTITY_ID,
    UPDATE_TYPE_ADDON,
    UPDATE_TYPE_CORE,
    UPDATE_TYPE_DEVICE,
    UPDATE_TYPE_HACS,
    UPDATE_TYPE_HAOS,
    UPDATE_TYPE_OTHER,
)

from .github_client import extract_owner_repo, fetch_pypi_release_date, fetch_release_date
from .storage import UpdateDateStorage

# GitHub release URL templates for entities that don't expose a GitHub release_url
_GITHUB_URL_TEMPLATES = {
    CORE_ENTITY_ID: "https://github.com/home-assistant/core/releases/tag/{version}",
    HAOS_ENTITY_ID: "https://github.com/home-assistant/operating-system/releases/tag/{version}",
}

_LOGGER = logging.getLogger(__name__)

TYPE_ORDER = {
    UPDATE_TYPE_CORE: 0,
    UPDATE_TYPE_HAOS: 1,
    UPDATE_TYPE_ADDON: 2,
    UPDATE_TYPE_HACS: 3,
    UPDATE_TYPE_DEVICE: 4,
    UPDATE_TYPE_OTHER: 5,
}


class UpdateManagerCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, storage: UpdateDateStorage) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=30),
        )
        self.storage = storage
        self.github_token: str | None = None

    async def _async_update_data(self) -> list[dict]:
        session = async_get_clientsession(self.hass)
        registry = er.async_get(self.hass)
        updates: list[dict] = []

        for state in self.hass.states.async_all("update"):
            if state.state != "on":
                continue

            entity_id = state.entity_id
            attrs = state.attributes
            new_version = attrs.get("latest_version") or ""
            current_version = attrs.get("installed_version") or ""
            release_url = attrs.get("release_url") or ""
            if not release_url or "github.com" not in release_url:
                template = _GITHUB_URL_TEMPLATES.get(entity_id)
                if template and new_version:
                    release_url = template.format(version=new_version)
            entry = registry.async_get(entity_id)
            title = (
                attrs.get("title")
                or (entry and (entry.name or entry.original_name))
                or self._format_entity_id(entity_id)
            )

            update_type = self._classify(state, entry)

            release_date = self.storage.get(entity_id, new_version)
            if not release_date and new_version:
                if entity_id == CORE_ENTITY_ID:
                    # PyPI is more reliable than GitHub API for HA Core (no rate limits)
                    release_date = await fetch_pypi_release_date(
                        session, "homeassistant", new_version
                    )
                elif "github.com" in release_url:
                    info = extract_owner_repo(release_url)
                    if info:
                        owner, repo = info
                        release_date = await fetch_release_date(
                            session, owner, repo, new_version, self.github_token
                        )
                if release_date:
                    await self.storage.async_set(entity_id, new_version, release_date)

            updates.append({
                "entity_id": entity_id,
                "title": title,
                "type": update_type,
                "installed_version": current_version,
                "latest_version": new_version,
                "release_date": release_date or "",
                "release_url": release_url,
                "skipped_version": attrs.get("skipped_version"),
                "in_progress": attrs.get("in_progress", False),
                "auto_update": attrs.get("auto_update", False),
            })

        return sorted(updates, key=lambda u: (TYPE_ORDER.get(u["type"], 99), u["title"].lower()))

    def _classify(self, state, entry: er.RegistryEntry | None) -> str:
        entity_id = state.entity_id

        if entity_id == CORE_ENTITY_ID:
            return UPDATE_TYPE_CORE
        if entity_id == HAOS_ENTITY_ID:
            return UPDATE_TYPE_HAOS

        if entry:
            if entry.platform == "hacs":
                return UPDATE_TYPE_HACS
            if entry.platform == "hassio":
                return UPDATE_TYPE_ADDON
            if entry.device_id:
                return UPDATE_TYPE_DEVICE

        return UPDATE_TYPE_OTHER

    @staticmethod
    def _format_entity_id(entity_id: str) -> str:
        """Turn 'update.some_thing_update' into 'Some Thing'."""
        name = entity_id.removeprefix("update.")
        if name.endswith("_update"):
            name = name[:-7]
        return name.replace("_", " ").title()
