"""WebSocket API commands for Advanced Update Manager."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    HACS_PLATFORMS,
    BACKUP_TYPE_FULL, BACKUP_TYPE_ADDON_ONLY, CONF_DEFAULT_BACKUP_TYPE,
    CONF_ENABLE_FEELING_LUCKY, CONF_HISTORY_KEEP_DAYS, DOMAIN,
    CORE_ENTITY_ID, HAOS_ENTITY_ID, HISTORY_KEEP_DAYS_DEFAULT, SUPERVISOR_ENTITY_ID,
    UPDATE_TYPE_ADDON, UPDATE_TYPE_CORE, UPDATE_TYPE_DEVICE,
    UPDATE_TYPE_HACS, UPDATE_TYPE_HAOS, UPDATE_TYPE_OTHER, UPDATE_TYPE_SUPERVISOR,
)
from .backup_helper import async_create_verified_backup
from .feeling_lucky import _async_reboot_required

_LOGGER = logging.getLogger(__name__)
_VERSION = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))["version"]


def _classify_entity(entity_id: str, entry: er.RegistryEntry | None) -> str:
    if entity_id == CORE_ENTITY_ID:
        return UPDATE_TYPE_CORE
    if entity_id == HAOS_ENTITY_ID:
        return UPDATE_TYPE_HAOS
    if entity_id == SUPERVISOR_ENTITY_ID:
        return UPDATE_TYPE_SUPERVISOR
    if entry:
        if entry.platform in HACS_PLATFORMS:
            return UPDATE_TYPE_HACS
        if entry.platform == "hassio":
            return UPDATE_TYPE_ADDON
        if entry.device_id:
            return UPDATE_TYPE_DEVICE
    return UPDATE_TYPE_OTHER



def async_setup(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get_updates)
    websocket_api.async_register_command(hass, ws_install_update)
    websocket_api.async_register_command(hass, ws_skip_update)
    websocket_api.async_register_command(hass, ws_get_skipped_updates)
    websocket_api.async_register_command(hass, ws_get_restart_info)
    websocket_api.async_register_command(hass, ws_get_config)
    websocket_api.async_register_command(hass, ws_get_installed)
    websocket_api.async_register_command(hass, ws_get_history)
    websocket_api.async_register_command(hass, ws_get_feeling_lucky_status)
    websocket_api.async_register_command(hass, ws_get_autopilot)
    websocket_api.async_register_command(hass, ws_save_autopilot)
    websocket_api.async_register_command(hass, ws_autopilot_run_now)
    websocket_api.async_register_command(hass, ws_autopilot_skip_next)
    websocket_api.async_register_command(hass, ws_autopilot_preview)
    websocket_api.async_register_command(hass, ws_autopilot_test_notify)


# ── Autopilot ─────────────────────────────────────────────────────────────

def _autopilot(hass: HomeAssistant):
    return (hass.data.get(DOMAIN) or {}).get("autopilot")


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/get_autopilot",
})
@websocket_api.async_response
async def ws_get_autopilot(hass: HomeAssistant, connection, msg: dict) -> None:
    """Everything the Autopilot tab needs in one round trip."""
    autopilot = _autopilot(hass)
    if autopilot is None:
        connection.send_error(msg["id"], "not_loaded", "Autopilot is not loaded")
        return
    connection.send_result(msg["id"], {
        **autopilot.snapshot(),
        "components": autopilot.list_components(),
        "available_targets": autopilot.available_targets(),
    })


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/save_autopilot",
    vol.Required("config"): dict,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save_autopilot(hass: HomeAssistant, connection, msg: dict) -> None:
    from .autopilot import sanitize_config  # noqa: PLC0415

    autopilot = _autopilot(hass)
    if autopilot is None:
        connection.send_error(msg["id"], "not_loaded", "Autopilot is not loaded")
        return
    await autopilot.async_save_config(sanitize_config(msg["config"]))
    connection.send_result(msg["id"], {**autopilot.snapshot(), "components": autopilot.list_components(),
                                       "available_targets": autopilot.available_targets()})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/autopilot_preview",
    vol.Required("config"): dict,
})
@websocket_api.async_response
async def ws_autopilot_preview(hass: HomeAssistant, connection, msg: dict) -> None:
    """Evaluate unsaved settings (nothing is stored) for the live preview."""
    from .autopilot import sanitize_config  # noqa: PLC0415

    autopilot = _autopilot(hass)
    if autopilot is None:
        connection.send_error(msg["id"], "not_loaded", "Autopilot is not loaded")
        return
    connection.send_result(msg["id"], {"evaluation": autopilot.evaluate(sanitize_config(msg["config"]))})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/autopilot_test_notify",
    vol.Required("targets"): [str],
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_autopilot_test_notify(hass: HomeAssistant, connection, msg: dict) -> None:
    """Send a test notification to the given (possibly unsaved) targets."""
    autopilot = _autopilot(hass)
    if autopilot is None:
        connection.send_error(msg["id"], "not_loaded", "Autopilot is not loaded")
        return
    connection.send_result(msg["id"], {"results": await autopilot.async_send_test(msg["targets"])})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/autopilot_run_now",
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_autopilot_run_now(hass: HomeAssistant, connection, msg: dict) -> None:
    autopilot = _autopilot(hass)
    if autopilot is None:
        connection.send_error(msg["id"], "not_loaded", "Autopilot is not loaded")
        return
    hass.async_create_task(autopilot.async_run(trigger="manual"))
    connection.send_result(msg["id"], {"started": True})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/autopilot_skip_next",
    vol.Optional("skip", default=True): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_autopilot_skip_next(hass: HomeAssistant, connection, msg: dict) -> None:
    autopilot = _autopilot(hass)
    if autopilot is None:
        connection.send_error(msg["id"], "not_loaded", "Autopilot is not loaded")
        return
    await autopilot.async_set_skip_next(msg["skip"])
    connection.send_result(msg["id"], {"skip_next": msg["skip"]})


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

    # The per-app (partial) backup only exists for apps/add-ons. For anything
    # else, fall back to a full backup rather than an entity backup that the
    # update entity may not support (HACS) or that isn't what was promised.
    if backup and backup_type == BACKUP_TYPE_ADDON_ONLY:
        entry = er.async_get(hass).async_get(entity_id)
        if _classify_entity(entity_id, entry) != UPDATE_TYPE_ADDON:
            backup_type = BACKUP_TYPE_FULL

    if backup and backup_type == BACKUP_TYPE_FULL:
        # Wait for a verified backup before installing anything. On HAOS the
        # old `backup.create` call silently did nothing (that service only
        # exists without Supervisor), so updates ran with no backup at all.
        ok, info = await async_create_verified_backup(hass)
        if not ok:
            _LOGGER.error("AUM: backup failed, %s was NOT updated: %s", entity_id, info)
            connection.send_error(msg["id"], "backup_failed", info)
            return
        _LOGGER.info("AUM: backup %s created, installing %s", info, entity_id)
        await hass.services.async_call(
            "update",
            "install",
            {"entity_id": entity_id, "backup": False},
            blocking=False,
        )
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
    history_keep_days = HISTORY_KEEP_DAYS_DEFAULT
    feeling_lucky_enabled = False
    if entries:
        default_backup_type = entries[0].options.get(CONF_DEFAULT_BACKUP_TYPE, BACKUP_TYPE_FULL)
        history_keep_days = entries[0].options.get(CONF_HISTORY_KEEP_DAYS, HISTORY_KEEP_DAYS_DEFAULT)
        feeling_lucky_enabled = entries[0].options.get(CONF_ENABLE_FEELING_LUCKY, False)
    storage = (hass.data.get(DOMAIN) or {}).get("storage")
    storage_size_bytes = storage.get_storage_size_bytes() if storage else 0
    connection.send_result(msg["id"], {
        "default_backup_type": default_backup_type,
        "history_keep_days": history_keep_days,
        "feeling_lucky_enabled": feeling_lucky_enabled,
        "storage_size_bytes": storage_size_bytes,
        "version": _VERSION,
    })


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/get_feeling_lucky_status",
})
@websocket_api.async_response
async def ws_get_feeling_lucky_status(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return the combined OPCON + queue state, straight from storage.

    Avoids the frontend having to guess sensor.opcon's actual entity_id
    (which depends on how HA generated it at first registration).
    """
    storage = (hass.data.get(DOMAIN) or {}).get("storage")
    connection.send_result(msg["id"], storage.get_feeling_lucky_state() if storage else {})


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
                    issue.domain in HACS_PLATFORMS  # "marketplace" on HA 2026.11+
                    and "restart" in issue.issue_id
                    and issue.dismissed_version is None
                ):
                    _LOGGER.debug("AUM restart: active repair issue found: %s/%s", issue.domain, issue.issue_id)
                    restart_required = True
                    break
        except Exception:  # noqa: BLE001
            pass

    # Independent of the HACS check above: Supervisor's own "reboot required"
    # repair (e.g. after a HAOS update — Supervisor no longer auto-reboots,
    # see home-assistant/supervisor#6978) is a completely different signal
    # that HACS's pending_restart never covers, AND needs a different action
    # (hassio.host_reboot reboots the host; homeassistant.restart only
    # restarts Core and would leave the repair in place).
    host_reboot_required = await _async_reboot_required(hass)
    if host_reboot_required:
        restart_required = True

    _LOGGER.debug("AUM restart_required=%s (hacs_check_done=%s)", restart_required, hacs_check_done)
    connection.send_result(msg["id"], {
        "restart_required": restart_required,
        "host_reboot_required": host_reboot_required,
    })


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/get_installed",
})
@websocket_api.async_response
async def ws_get_installed(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return all update entities that are currently up to date (state=off)."""
    registry = er.async_get(hass)
    storage = (hass.data.get(DOMAIN) or {}).get("storage")
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
        installed_version = attrs.get("installed_version") or ""
        update_type = _classify_entity(entity_id, entry)
        release_date = (storage.get(entity_id, installed_version) if storage and installed_version else "") or ""
        installed.append({
            "entity_id": entity_id,
            "title": title,
            "installed_version": installed_version,
            "type": update_type,
            "release_date": release_date,
            "release_url": attrs.get("release_url") or "",
            "install_date": "",
        })

    for item in installed:
        event = storage.get_latest_install(item["entity_id"]) if storage else None
        item["install_date"] = event["install_date"] if event else ""

    installed.sort(key=lambda x: x["title"].lower())
    connection.send_result(msg["id"], {"installed": installed})


_RELEASE_URL_TEMPLATES = {
    CORE_ENTITY_ID: "https://github.com/home-assistant/core/releases/tag/{version}",
    HAOS_ENTITY_ID: "https://github.com/home-assistant/operating-system/releases/tag/{version}",
}


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/get_history",
})
@websocket_api.async_response
async def ws_get_history(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return update install events from AUM's own persistent storage."""
    storage = (hass.data.get(DOMAIN) or {}).get("storage")
    events: list[dict] = []
    oldest_date: str | None = None

    if storage:
        history = storage.get_install_history()  # newest first
        for e in history:
            entity_id = e["entity_id"]
            to_version = e.get("to_version", "")
            release_url = ""
            if entity_id in _RELEASE_URL_TEMPLATES and to_version:
                release_url = _RELEASE_URL_TEMPLATES[entity_id].format(version=to_version)
            else:
                state = hass.states.get(entity_id)
                release_url = (state.attributes.get("release_url") or "") if state else ""
            events.append({
                "entity_id": entity_id,
                "title": e["title"],
                "type": e.get("type", "other"),
                "from_version": e.get("from_version", ""),
                "to_version": to_version,
                "date": e["install_date"],
                "datetime": e["install_date"] + "T12:00:00+00:00",
                "release_url": release_url,
            })
        if events:
            oldest_date = events[-1]["date"]  # last item is oldest (list is newest-first)

    connection.send_result(msg["id"], {
        "events": events,
        "oldest_date": oldest_date,
        "recorder_available": True,
    })
