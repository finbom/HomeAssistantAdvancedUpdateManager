"""WebSocket API commands for Advanced Update Manager."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import BACKUP_TYPE_FULL, BACKUP_TYPE_ADDON_ONLY, CONF_DEFAULT_BACKUP_TYPE, DOMAIN


def async_setup(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get_updates)
    websocket_api.async_register_command(hass, ws_install_update)
    websocket_api.async_register_command(hass, ws_skip_update)
    websocket_api.async_register_command(hass, ws_get_skipped_updates)
    websocket_api.async_register_command(hass, ws_get_restart_info)
    websocket_api.async_register_command(hass, ws_get_config)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/get_updates",
})
@websocket_api.async_response
async def ws_get_updates(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return all pending updates with enriched metadata."""
    coordinator = hass.data[DOMAIN]["coordinator"]
    await coordinator.async_refresh()
    connection.send_result(msg["id"], {"updates": coordinator.data or []})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/install_update",
    vol.Required("entity_id"): str,
    vol.Optional("backup", default=False): bool,
    vol.Optional("backup_type", default=BACKUP_TYPE_FULL): vol.In([BACKUP_TYPE_FULL, BACKUP_TYPE_ADDON_ONLY]),
})
@websocket_api.async_response
async def ws_install_update(hass: HomeAssistant, connection, msg: dict) -> None:
    """Trigger update.install for the given entity, with optional full backup first."""
    backup = msg["backup"]
    backup_type = msg.get("backup_type", BACKUP_TYPE_FULL)
    entity_id = msg["entity_id"]

    if backup and backup_type == BACKUP_TYPE_FULL:
        async def _full_backup_then_install() -> None:
            if hass.services.has_service("backup", "create"):
                await hass.services.async_call("backup", "create", {}, blocking=True)
            await hass.services.async_call(
                "update",
                "install",
                {"entity_id": entity_id, "backup": False},
                blocking=False,
            )

        hass.async_create_task(_full_backup_then_install())
    else:
        await hass.services.async_call(
            "update",
            "install",
            {"entity_id": entity_id, "backup": backup},
            blocking=False,
        )

    connection.send_result(msg["id"], {"success": True})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/get_config",
})
@websocket_api.async_response
async def ws_get_config(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return integration options relevant to the frontend."""
    entries = hass.config_entries.async_entries(DOMAIN)
    default_backup_type = BACKUP_TYPE_FULL
    if entries:
        default_backup_type = entries[0].options.get(CONF_DEFAULT_BACKUP_TYPE, BACKUP_TYPE_FULL)
    connection.send_result(msg["id"], {"default_backup_type": default_backup_type})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/skip_update",
    vol.Required("entity_id"): str,
})
@websocket_api.async_response
async def ws_skip_update(hass: HomeAssistant, connection, msg: dict) -> None:
    """Trigger update.skip for the given entity."""
    await hass.services.async_call(
        "update",
        "skip",
        {"entity_id": msg["entity_id"]},
        blocking=False,
    )
    connection.send_result(msg["id"], {"success": True})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/get_skipped_updates",
})
@websocket_api.async_response
async def ws_get_skipped_updates(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return update entities whose current latest version has been skipped."""
    registry = er.async_get(hass)
    skipped = []

    for state in hass.states.async_all("update"):
        attrs = state.attributes
        skipped_version = attrs.get("skipped_version")
        if not skipped_version:
            continue

        entity_id = state.entity_id
        entry = registry.async_get(entity_id)
        title = (
            attrs.get("title")
            or (entry and (entry.name or entry.original_name))
            or entity_id.removeprefix("update.")
        )

        skipped.append({
            "entity_id": entity_id,
            "title": title,
            "installed_version": attrs.get("installed_version") or "",
            "skipped_version": skipped_version,
            "release_url": attrs.get("release_url") or "",
        })

    connection.send_result(msg["id"], {"updates": skipped})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/get_restart_info",
})
@websocket_api.async_response
async def ws_get_restart_info(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return whether a restart is pending."""
    restart_required = False

    # Check HA repair issues (HA 2022.9+) — language-independent
    try:
        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415
        registry = ir.async_get(hass)
        for (_, issue_id) in registry.issues:
            if issue_id.lower().endswith("restart_required"):
                restart_required = True
                break
    except Exception:  # noqa: BLE001
        pass

    # Fallback: check persistent notifications by known IDs
    if not restart_required:
        _RESTART_NOTIF_IDS = frozenset({
            "homeassistant_restart",
            "home_assistant_restart",
            "hacs_restart",
            "restart_required",
        })
        for state in hass.states.async_all("persistent_notification"):
            notif_id = state.entity_id.removeprefix("persistent_notification.")
            if notif_id in _RESTART_NOTIF_IDS:
                restart_required = True
                break

    connection.send_result(msg["id"], {"restart_required": restart_required})
