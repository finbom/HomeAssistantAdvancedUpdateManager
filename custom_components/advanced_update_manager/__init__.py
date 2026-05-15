"""Advanced Update Manager — custom panel with enriched update info."""
from __future__ import annotations

import logging
import os

from homeassistant.components.panel_custom import async_register_panel
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_JS,
    PANEL_TITLE,
    PANEL_URL_PATH,
)
from .coordinator import UpdateManagerCoordinator
from .storage import UpdateDateStorage
from . import websocket_api

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Advanced Update Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    storage = UpdateDateStorage(hass)
    await storage.async_load()

    coordinator = UpdateManagerCoordinator(hass, storage)
    hass.data[DOMAIN]["coordinator"] = coordinator
    hass.data[DOMAIN]["storage"] = storage

    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
    hass.http.register_static_path(
        f"/{DOMAIN}_panel",
        frontend_dir,
        cache_headers=False,
    )

    await async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        js_url=f"/{DOMAIN}_panel/{PANEL_JS}",
        require_admin=False,
        trust_external_script=False,
    )

    websocket_api.async_setup(hass)

    await coordinator.async_refresh()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].clear()
    return True
