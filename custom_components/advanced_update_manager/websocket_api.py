"""WebSocket API commands for Advanced Update Manager."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import DOMAIN


def async_setup(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get_updates)
    websocket_api.async_register_command(hass, ws_install_update)
    websocket_api.async_register_command(hass, ws_skip_update)


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
})
@websocket_api.async_response
async def ws_install_update(hass: HomeAssistant, connection, msg: dict) -> None:
    """Trigger update.install for the given entity."""
    await hass.services.async_call(
        "update",
        "install",
        {"entity_id": msg["entity_id"], "backup": msg["backup"]},
        blocking=False,
    )
    connection.send_result(msg["id"], {"success": True})


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
