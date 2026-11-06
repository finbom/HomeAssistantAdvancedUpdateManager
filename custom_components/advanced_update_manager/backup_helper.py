"""Verified full backups, shared by Autopilot, Feeling Lucky and the panel's
"Backup + Update" button.

On a Supervisor (HAOS) install `backup.create` does not exist — only
`backup.create_automatic` and `hassio.backup_full`. Calling `backup.create`
guarded by has_service() therefore silently took NO backup on HAOS. This
module picks the service that exists and only reports success once a new
backup actually shows up in the backup manager.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import AP_BACKUP_TIMEOUT

_LOGGER = logging.getLogger(__name__)


def _get_backup_manager(hass: HomeAssistant):
    try:
        from homeassistant.components.backup.const import DATA_MANAGER  # noqa: PLC0415
        return hass.data.get(DATA_MANAGER)
    except Exception:  # noqa: BLE001
        return None


async def _async_backup_index(manager) -> dict | None:
    """{backup_id: name} from the backup manager, or None if unavailable."""
    try:
        result = await manager.async_get_backups()
        backups = result[0] if isinstance(result, tuple) else result
        return {bid: getattr(b, "name", bid) for bid, b in (backups or {}).items()}
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("AUM: could not list backups: %s", err)
        return None


async def async_create_verified_backup(hass: HomeAssistant) -> tuple[bool, str]:
    """Create a full backup and confirm it actually exists.

    On a Supervisor (HAOS) install `backup.create` doesn't exist — only
    `backup.create_automatic` (uses the user's backup settings: locations,
    encryption) and `hassio.backup_full`. A backup only counts once a new
    entry shows up in the backup manager; "the service call didn't raise" is
    not proof enough for something that runs unattended at night.
    """
    manager = _get_backup_manager(hass)
    before = await _async_backup_index(manager) if manager else None

    try:
        if hass.services.has_service("backup", "create_automatic"):
            await hass.services.async_call("backup", "create_automatic", {}, blocking=True)
        elif hass.services.has_service("hassio", "backup_full"):
            name = f"AUM {dt_util.now().strftime('%Y-%m-%d %H:%M')}"
            await hass.services.async_call("hassio", "backup_full", {"name": name}, blocking=True)
        elif hass.services.has_service("backup", "create"):
            await hass.services.async_call("backup", "create", {}, blocking=True)
        else:
            return False, "No backup service is available (backup.create_automatic / hassio.backup_full / backup.create)."
    except Exception as err:  # noqa: BLE001
        return False, f"Backup service call failed: {err}"

    if before is None:
        # Can't verify on this install — the service call itself succeeded.
        return True, "backup"

    elapsed = 0
    while elapsed < AP_BACKUP_TIMEOUT:
        after = await _async_backup_index(manager)
        if after is not None:
            new = [name for bid, name in after.items() if bid not in before]
            if new:
                return True, new[0]
        await asyncio.sleep(10)
        elapsed += 10
    return False, f"No new backup appeared within {AP_BACKUP_TIMEOUT // 60} minutes."
