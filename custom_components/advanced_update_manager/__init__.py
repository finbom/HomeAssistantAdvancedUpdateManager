"""Advanced Update Manager — custom panel with enriched update info."""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path

from homeassistant.components.frontend import async_remove_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.panel_custom import async_register_panel
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_HISTORY_KEEP_DAYS,
    CONF_SHOW_IN_SIDEBAR,
    DOMAIN,
    HISTORY_KEEP_DAYS_DEFAULT,
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
_VERSION = json.loads((Path(__file__).parent / "manifest.json").read_text())["version"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Advanced Update Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    storage = UpdateDateStorage(hass)
    await storage.async_load()

    keep_days = entry.options.get(CONF_HISTORY_KEEP_DAYS, HISTORY_KEEP_DAYS_DEFAULT)
    await storage.async_cleanup(keep_days)

    coordinator = UpdateManagerCoordinator(hass, storage)
    hass.data[DOMAIN]["coordinator"] = coordinator
    hass.data[DOMAIN]["storage"] = storage

    if not hass.data[DOMAIN].get("static_paths_registered"):
        frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
        await hass.http.async_register_static_paths([
            StaticPathConfig(f"/{DOMAIN}_panel", frontend_dir, cache_headers=False),
        ])
        hass.data[DOMAIN]["static_paths_registered"] = True

    show_in_sidebar = entry.options.get(CONF_SHOW_IN_SIDEBAR, True)
    await async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=PANEL_TITLE if show_in_sidebar else None,
        sidebar_icon=PANEL_ICON if show_in_sidebar else None,
        js_url=f"/{DOMAIN}_panel/{PANEL_JS}?v={_VERSION}",
        require_admin=False,
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    # Listen for update installs (on → off transitions on update.* entities)
    async def _on_state_changed(event: Event) -> None:
        entity_id = event.data.get("entity_id", "")
        if not entity_id.startswith("update."):
            return
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not old_state or not new_state:
            return
        if old_state.state != "on" or new_state.state != "off":
            return

        domain_storage = hass.data.get(DOMAIN, {}).get("storage")
        if not domain_storage:
            return

        registry = er.async_get(hass)
        reg_entry = registry.async_get(entity_id)
        attrs_new = new_state.attributes or {}
        attrs_old = old_state.attributes or {}
        title = (
            attrs_new.get("title")
            or (reg_entry and (reg_entry.name or reg_entry.original_name))
            or entity_id.removeprefix("update.")
        )
        update_type = websocket_api._classify_entity(entity_id, reg_entry)
        from_version = attrs_old.get("installed_version") or ""
        to_version = attrs_new.get("installed_version") or attrs_old.get("latest_version") or ""
        install_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        await domain_storage.async_add_install_event(
            entity_id, title, update_type, from_version, to_version, install_date
        )
        _LOGGER.debug("AUM tracked install: %s %s → %s", entity_id, from_version, to_version)

        coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
        if coordinator:
            hass.async_create_task(coordinator.async_request_refresh())

    entry.async_on_unload(hass.bus.async_listen("state_changed", _on_state_changed))

    websocket_api.async_setup(hass)
    await coordinator.async_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    # Seed history from recorder (one-time, runs in background)
    if not storage.is_history_seeded():
        hass.async_create_task(_async_seed_history_from_recorder(hass, storage))

    return True


async def _async_seed_history_from_recorder(hass: HomeAssistant, storage: UpdateDateStorage) -> None:
    """One-time retroactive scan of recorder to populate install history."""
    events: list[dict] = []
    try:
        from homeassistant.components.recorder import get_instance  # noqa: PLC0415
        from homeassistant.components.recorder.history import get_significant_states  # noqa: PLC0415
        from homeassistant.util import dt as dt_util  # noqa: PLC0415

        instance = get_instance(hass)
        start_time = dt_util.utcnow() - datetime.timedelta(days=365)
        entity_ids = [s.entity_id for s in hass.states.async_all("update")]

        if entity_ids:
            states_dict = await instance.async_add_executor_job(
                get_significant_states, hass, start_time, None, entity_ids
            )
            registry = er.async_get(hass)
            for entity_id, state_list in states_dict.items():
                reg_entry = registry.async_get(entity_id)
                for i in range(1, len(state_list)):
                    prev = state_list[i - 1]
                    curr = state_list[i]
                    if getattr(prev, "state", None) != "on" or getattr(curr, "state", None) != "off":
                        continue
                    prev_attrs = getattr(prev, "attributes", {}) or {}
                    curr_attrs = getattr(curr, "attributes", {}) or {}
                    changed = getattr(curr, "last_changed", None)
                    if not changed:
                        continue
                    title = (
                        curr_attrs.get("title")
                        or (reg_entry and (reg_entry.name or reg_entry.original_name))
                        or entity_id.removeprefix("update.")
                    )
                    events.append({
                        "entity_id": entity_id,
                        "title": title,
                        "type": websocket_api._classify_entity(entity_id, reg_entry),
                        "from_version": prev_attrs.get("installed_version") or "",
                        "to_version": curr_attrs.get("installed_version") or prev_attrs.get("latest_version") or "",
                        "install_date": changed.strftime("%Y-%m-%d"),
                    })

    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("AUM history seed: recorder unavailable: %s", err)

    added = await storage.async_seed_history(events)
    _LOGGER.debug("AUM history seeded from recorder: %d events added", added)


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    async_remove_panel(hass, PANEL_URL_PATH)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok:
        static_flag = hass.data[DOMAIN].get("static_paths_registered")
        hass.data[DOMAIN].clear()
        if static_flag:
            hass.data[DOMAIN]["static_paths_registered"] = True
    return unload_ok
