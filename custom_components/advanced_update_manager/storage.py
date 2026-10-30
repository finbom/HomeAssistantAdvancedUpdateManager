"""Persistent storage for release dates and install history."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import FL_OPCON_OK, SIGNAL_FEELING_LUCKY_UPDATED, STORAGE_KEY, STORAGE_VERSION

_FL_DEFAULT = {
    "opcon": FL_OPCON_OK,
    "last_error": None,
    "since": None,
    "backup_taken": None,
    "queue": {
        "steps": [],
        "current_item": None,
        "started_at": None,
    },
}


class UpdateDateStorage:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._hass = hass
        self._dates: dict[str, str] = {}
        self._urls: dict[str, str] = {}
        self._install_history: list[dict] = []
        self._history_seeded: bool = False
        self._feeling_lucky: dict = dict(_FL_DEFAULT)

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._dates = data.get("dates", {})
        self._urls = data.get("urls", {})
        self._install_history = data.get("install_history", [])
        self._history_seeded = data.get("history_seeded", False)
        self._feeling_lucky = {**_FL_DEFAULT, **data.get("feeling_lucky", {})}

    async def _async_save(self) -> None:
        await self._store.async_save({
            "dates": self._dates,
            "urls": self._urls,
            "install_history": self._install_history,
            "history_seeded": self._history_seeded,
            "feeling_lucky": self._feeling_lucky,
        })

    # --- Feeling Lucky: OPCON + queue (frozen together, reset together) ---

    def get_feeling_lucky_state(self) -> dict:
        """Return a copy of the combined OPCON + queue state."""
        return dict(self._feeling_lucky)

    async def async_save_feeling_lucky_state(self, state: dict) -> None:
        """Persist the full OPCON + queue state. Called before every step runs
        so a run survives HA dying mid-update (e.g. a Core/OS restart)."""
        self._feeling_lucky = dict(state)
        await self._async_save()
        async_dispatcher_send(self._hass, SIGNAL_FEELING_LUCKY_UPDATED)

    async def async_reset_feeling_lucky_state(self) -> None:
        """'Reset OPCON' — the only way back to green. Clears both the status
        and the frozen queue snapshot together."""
        self._feeling_lucky = dict(_FL_DEFAULT)
        await self._async_save()
        async_dispatcher_send(self._hass, SIGNAL_FEELING_LUCKY_UPDATED)

    # --- Release dates ---

    def get(self, entity_id: str, version: str) -> str | None:
        return self._dates.get(f"{entity_id}|{version}")

    def get_url(self, entity_id: str, version: str) -> str | None:
        """Return cached release URL, or None if never looked up.

        Empty string means we looked up and found no confirmed tag (sentinel to
        avoid retrying on every coordinator cycle).
        """
        key = f"{entity_id}|{version}"
        if key not in self._urls:
            return None
        return self._urls[key]

    async def async_set(self, entity_id: str, version: str, date: str, url: str | None = None) -> None:
        key = f"{entity_id}|{version}"
        self._dates[key] = date
        if url is not None:
            self._urls[key] = url
        await self._async_save()

    # --- Install history ---

    def get_install_history(self) -> list[dict]:
        return list(reversed(self._install_history))

    def get_latest_install(self, entity_id: str) -> dict | None:
        for event in reversed(self._install_history):
            if event.get("entity_id") == entity_id:
                return event
        return None

    async def async_add_install_event(
        self,
        entity_id: str,
        title: str,
        update_type: str,
        from_version: str,
        to_version: str,
        install_date: str,
    ) -> None:
        self._install_history.append({
            "entity_id": entity_id,
            "title": title,
            "type": update_type,
            "from_version": from_version,
            "to_version": to_version,
            "install_date": install_date,
        })
        await self._async_save()

    async def async_seed_history(self, events: list[dict]) -> int:
        """Bulk-insert events from retroactive recorder scan, deduplicating."""
        existing = {
            (e["entity_id"], e["install_date"], e.get("to_version", ""))
            for e in self._install_history
        }
        added = 0
        for ev in events:
            key = (ev["entity_id"], ev["install_date"], ev.get("to_version", ""))
            if key not in existing:
                self._install_history.append(ev)
                existing.add(key)
                added += 1
        self._install_history.sort(key=lambda e: e.get("install_date", ""))
        self._history_seeded = True
        await self._async_save()
        return added

    def is_history_seeded(self) -> bool:
        return self._history_seeded

    # --- Retention cleanup ---

    async def async_cleanup(self, keep_days: int) -> None:
        if keep_days <= 0:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        before = len(self._install_history)
        self._install_history = [
            e for e in self._install_history if e.get("install_date", "") >= cutoff
        ]
        if len(self._install_history) < before:
            await self._async_save()

    # --- Storage size ---

    def get_storage_size_bytes(self) -> int:
        try:
            return os.path.getsize(self._hass.config.path(".storage", STORAGE_KEY))
        except OSError:
            return 0
