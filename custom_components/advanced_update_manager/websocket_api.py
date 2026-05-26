"""WebSocket API commands for Advanced Update Manager."""
from __future__ import annotations

import datetime
import logging
import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import BACKUP_TYPE_FULL, BACKUP_TYPE_ADDON_ONLY, CONF_DEFAULT_BACKUP_TYPE, DOMAIN

_LOGGER = logging.getLogger(__name__)


def async_setup(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get_updates)
    websocket_api.async_register_command(hass, ws_install_update)
    websocket_api.async_register_command(hass, ws_skip_update)
    websocket_api.async_register_command(hass, ws_get_skipped_updates)
    websocket_api.async_register_command(hass, ws_get_restart_info)
    websocket_api.async_register_command(hass, ws_get_config)
    websocket_api.async_register_command(hass, ws_get_installed)
    websocket_api.async_register_command(hass, ws_get_history)


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
    hacs_check_done = False

    # Primary: ask HACS runtime data directly — repo.pending_restart reflects current state
    # and is reset on startup, unlike repair issues which persist on disk across restarts.
    try:
        hacs = hass.data.get("hacs")
        if hacs is not None:
            repos = getattr(hacs, "repositories", None)
            if repos is not None:
                for repo in getattr(repos, "list_all", []):
                    if getattr(repo, "pending_restart", False):
                        repo_name = getattr(getattr(repo, "data", None), "full_name", "?")
                        _LOGGER.debug("AUM restart: HACS repo %s has pending_restart", repo_name)
                        restart_required = True
                        break
                hacs_check_done = True
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("AUM restart: HACS direct check failed: %s", err)

    # Fallback: repair issues (only when HACS runtime data was unavailable)
    # Repair issues can be stale — they survive HA restarts even when no restart is needed.
    if not hacs_check_done:
        try:
            from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415
            registry = ir.async_get(hass)
            for issue in registry.issues.values():
                _LOGGER.debug(
                    "AUM restart: fallback issue %s/%s dismissed=%s",
                    issue.domain, issue.issue_id, issue.dismissed_version,
                )
                if (
                    issue.domain == "hacs"
                    and issue.issue_id.startswith("restart_required")
                    and issue.dismissed_version is None
                ):
                    _LOGGER.debug("AUM restart: active repair issue found: %s/%s", issue.domain, issue.issue_id)
                    restart_required = True
                    break
        except Exception:  # noqa: BLE001
            pass

    _LOGGER.debug("AUM restart_required=%s (hacs_check_done=%s)", restart_required, hacs_check_done)
    connection.send_result(msg["id"], {"restart_required": restart_required})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/get_installed",
})
@websocket_api.async_response
async def ws_get_installed(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return all update entities that are currently up to date (state=off)."""
    registry = er.async_get(hass)
    installed = []
    for state in hass.states.async_all("update"):
        if state.state != "off":
            continue
        attrs = state.attributes
        entity_id = state.entity_id
        entry = registry.async_get(entity_id)
        title = (
            attrs.get("title")
            or (entry and (entry.name or entry.original_name))
            or entity_id.removeprefix("update.")
        )
        installed.append({
            "entity_id": entity_id,
            "title": title,
            "installed_version": attrs.get("installed_version") or "",
        })
    installed.sort(key=lambda x: x["title"].lower())
    connection.send_result(msg["id"], {"installed": installed})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/get_history",
})
@websocket_api.async_response
async def ws_get_history(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return update install events from recorder history (on→off transitions)."""
    events: list[dict] = []
    oldest_dt: datetime.datetime | None = None
    recorder_available = False

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
            recorder_available = True
            registry = er.async_get(hass)

            for entity_id, state_list in states_dict.items():
                entry = registry.async_get(entity_id)
                for i in range(1, len(state_list)):
                    prev = state_list[i - 1]
                    curr = state_list[i]
                    if getattr(prev, "state", None) != "on" or getattr(curr, "state", None) != "off":
                        continue
                    prev_attrs = getattr(prev, "attributes", {}) or {}
                    curr_attrs = getattr(curr, "attributes", {}) or {}
                    title = (
                        curr_attrs.get("title")
                        or (entry and (entry.name or entry.original_name))
                        or entity_id.removeprefix("update.")
                    )
                    changed = getattr(curr, "last_changed", None)
                    if not changed:
                        continue
                    if oldest_dt is None or changed < oldest_dt:
                        oldest_dt = changed
                    events.append({
                        "entity_id": entity_id,
                        "title": title,
                        "from_version": prev_attrs.get("installed_version") or "",
                        "to_version": curr_attrs.get("installed_version") or prev_attrs.get("latest_version") or "",
                        "date": changed.strftime("%Y-%m-%d"),
                        "datetime": changed.isoformat(),
                    })
        else:
            recorder_available = True

    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("AUM history: recorder query failed: %s", err)

    events.sort(key=lambda x: x["datetime"], reverse=True)
    connection.send_result(msg["id"], {
        "events": events,
        "oldest_date": oldest_dt.strftime("%Y-%m-%d") if oldest_dt else None,
        "recorder_available": recorder_available,
    })
