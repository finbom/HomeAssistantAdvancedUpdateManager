"""Autopilot — scheduled, unattended update runs.

Autopilot decides *what* to install and *when*; the actual sequence is handed
to FeelingLuckyRunner (origin="autopilot"), so ordering, persistence across
restarts, per-phase timeouts and Code Red behave exactly like Feeling Lucky.

Flow of one run:
  schedule fires -> "skip tonight"? -> Code Red / busy? -> evaluate filters
  -> verified full backup (abort run if it fails, no Code Red: nothing was
  changed yet, the next scheduled run simply tries again) -> FeelingLuckyRunner
  (HACS -> add-ons -> Core; AUM itself last in HACS) -> summary, and a
  homeassistant.restart if HACS left a restart pending and Core didn't already
  restart HA.

Only Core, add-ons (apps) and HACS are supported. Supervisor, OS, device
firmware and anything else are never touched by Autopilot.
"""
from __future__ import annotations

import copy
import logging
from datetime import date, datetime, time, timedelta

from homeassistant.const import MAJOR_VERSION, MINOR_VERSION
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import (
    AP_ACTION_SKIP,
    AP_BUS_EVENT,
    AP_EVENT_BACKUP_FAILED,
    AP_EVENT_BACKUP_OK,
    AP_EVENT_NEW_COMPONENTS,
    AP_EVENT_NOTHING,
    AP_EVENT_PREWARN,
    AP_EVENT_RESTARTED,
    AP_EVENT_START,
    AP_EVENT_SUMMARY,
    AP_EVENT_UPDATE_FAILED,
    AP_EVENTS_LOCKED_ON,
    AP_MODE_ALL,
    AP_MODE_ALL_EXCEPT,
    AP_MODE_OFF,
    AP_MODE_ONLY,
    AP_ORIGIN,
    AP_STATUS_BACKUP_FAILED,
    AP_STATUS_BLOCKED,
    AP_STATUS_FAILED,
    AP_STATUS_NOTHING,
    AP_STATUS_RUNNING,
    AP_STATUS_SKIPPED,
    AP_STATUS_SUCCESS,
    AP_TARGET_PERSISTENT,
    AP_TYPES,
    FL_OPCON_OK,
    FL_PHASE_CORE,
    FL_PHASE_HACS,
    FL_STEP_DONE,
    FL_STEP_FAILED,
    FL_STEP_INSTALLING,
    SIGNAL_AUTOPILOT_UPDATED,
    UPDATE_TYPE_ADDON,
    UPDATE_TYPE_CORE,
    UPDATE_TYPE_HACS,
)
from .backup_helper import async_create_verified_backup
from .feeling_lucky import FeelingLuckyRunner, hacs_update_kind, is_self_update_entity
from .storage import UpdateDateStorage

_LOGGER = logging.getLogger(__name__)

# Reasons an update is not part of the next run (exposed in sensor attributes
# and the panel — keep them stable, they're effectively API).
REASON_TYPE_OFF = "type_off"
REASON_EXCLUDED = "excluded"
REASON_NOT_SELECTED = "not_selected"
REASON_NOT_INSTALLABLE = "not_installable"
REASON_IN_PROGRESS = "in_progress"
REASON_MAJOR = "major_version"
REASON_TOO_NEW = "too_new"

_TEXT = {
    "en": {
        "skip_action": "Skip tonight",
        "prewarn_title": "Autopilot: run at {time}",
        "prewarn_msg": "{count} update(s) will be installed at {time}:\n{items}",
        "start_title": "Autopilot engaged",
        "start_msg": "Taking a full backup, then installing {count} update(s):\n{items}",
        "backup_ok_title": "Autopilot: backup done",
        "backup_ok_msg": "Backup \"{name}\" created. Installing updates now.",
        "backup_failed_title": "Autopilot disengaged",
        "backup_failed_msg": "The backup failed, so nothing was installed.\n\n{error}\n\nAutopilot will try again at the next scheduled run.",
        "failed_title": "Autopilot disengaged — Code Red",
        "failed_msg": "{error}\n\nInstalled: {done}\nNot started: {not_started}\n\nOpen Update Manager and reset OPCON before Autopilot can run again.",
        "summary_title": "Autopilot: {count} update(s) installed",
        "summary_msg": "Installed:\n{items}",
        "summary_restart": "Restarting Home Assistant to finish the HACS updates.",
        "summary_core": "Home Assistant was restarted by the Core update.",
        "summary_waiting": "Waiting (too new): {count}",
        "summary_major": "Skipped (major version): {count}",
        "restarted_title": "Autopilot: Home Assistant is back",
        "restarted_msg": "The restart after the Autopilot run is complete.",
        "nothing_title": "Autopilot: no updates to install",
        "nothing_msg": "No updates were identified as needed, so nothing was installed. Waiting for minimum age: {waiting}.",
        "skipped_title": "Autopilot: run skipped",
        "skipped_msg": "The scheduled run was skipped as requested.",
        "blocked_title": "Autopilot disengaged",
        "blocked_code_red": "OPCON is Code Red. Reset it in Update Manager before Autopilot can run again.",
        "blocked_busy": "Another update run was already in progress.",
        "new_title": "Autopilot: new components not in the schedule",
        "new_msg": "These were installed after you last saved the Autopilot settings and are not included (\"Only selected\" mode):\n{items}",
        "none": "none",
        "target_persistent": "Home Assistant notifications",
        "sender": "From Home Assistant ({instance}) · Advanced Update Manager Autopilot",
        "test_title": "Autopilot: test notification",
        "test_msg": "If you can read this, Autopilot notifications reach you here.",
    },
    "sv": {
        "skip_action": "Hoppa över i natt",
        "prewarn_title": "Autopilot: körning kl {time}",
        "prewarn_msg": "{count} uppdatering(ar) installeras kl {time}:\n{items}",
        "start_title": "Autopilot engaged",
        "start_msg": "Tar full backup och installerar sedan {count} uppdatering(ar):\n{items}",
        "backup_ok_title": "Autopilot: backup klar",
        "backup_ok_msg": "Backup \"{name}\" skapad. Installerar uppdateringar nu.",
        "backup_failed_title": "Autopilot disengaged",
        "backup_failed_msg": "Backupen misslyckades, så ingenting installerades.\n\n{error}\n\nAutopilot försöker igen vid nästa schemalagda körning.",
        "failed_title": "Autopilot disengaged — Code Red",
        "failed_msg": "{error}\n\nInstallerade: {done}\nStartade aldrig: {not_started}\n\nÖppna Update Manager och återställ OPCON innan Autopilot kan köra igen.",
        "summary_title": "Autopilot: {count} uppdatering(ar) installerade",
        "summary_msg": "Installerade:\n{items}",
        "summary_restart": "Startar om Home Assistant för att slutföra HACS-uppdateringarna.",
        "summary_core": "Home Assistant startades om av Core-uppdateringen.",
        "summary_waiting": "Väntar (för nya): {count}",
        "summary_major": "Överhoppade (major-version): {count}",
        "restarted_title": "Autopilot: Home Assistant är tillbaka",
        "restarted_msg": "Omstarten efter Autopilot-körningen är klar.",
        "nothing_title": "Autopilot: inga uppdateringar att installera",
        "nothing_msg": "Inga uppdateringar behövde installeras, så ingenting installerades. Väntar på minimiålder: {waiting}.",
        "skipped_title": "Autopilot: körning överhoppad",
        "skipped_msg": "Den schemalagda körningen hoppades över som du bad om.",
        "blocked_title": "Autopilot disengaged",
        "blocked_code_red": "OPCON är Code Red. Återställ det i Update Manager innan Autopilot kan köra igen.",
        "blocked_busy": "En annan uppdateringskörning pågick redan.",
        "new_title": "Autopilot: nya komponenter som inte ingår i schemat",
        "new_msg": "De här installerades efter att du senast sparade Autopilot-inställningarna och ingår inte (läget \"Endast valda\"):\n{items}",
        "none": "inga",
        "target_persistent": "Home Assistant-notiser",
        "sender": "Från Home Assistant ({instance}) · Advanced Update Manager Autopilot",
        "test_title": "Autopilot: testnotis",
        "test_msg": "Kan du läsa det här når Autopilots notiser dig här.",
    },
}


def _today() -> date:
    return dt_util.now().date()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _hacs_restart_pending(hass: HomeAssistant) -> bool | None:
    """True/False from HACS runtime data, None if HACS couldn't be asked."""
    try:
        hacs = hass.data.get("hacs")
        repos = getattr(hacs, "repositories", None) if hacs is not None else None
        if repos is None:
            return None
        return any(getattr(repo, "pending_restart", False) for repo in getattr(repos, "list_all", []))
    except Exception:  # noqa: BLE001
        return None


class AutopilotManager:
    """Owns the Autopilot schedule, filters and run bookkeeping."""

    def __init__(
        self,
        hass: HomeAssistant,
        storage: UpdateDateStorage,
        runner: FeelingLuckyRunner,
        coordinator,
    ) -> None:
        self.hass = hass
        self.storage = storage
        self.runner = runner
        self.coordinator = coordinator
        self._unsubs: list = []
        self._schedule_unsubs: list = []
        self._next_run: datetime | None = None
        self._busy = False

    # ---- Lifecycle -------------------------------------------------------

    async def async_setup(self) -> None:
        # Registered before the runner gets a chance to resume a run on
        # homeassistant_started, so a run interrupted by a Core update still
        # reports back to Autopilot when it finishes.
        self._unsubs.append(self.runner.add_finish_listener(self._async_on_run_finished))
        self._unsubs.append(
            self.hass.bus.async_listen("mobile_app_notification_action", self._on_mobile_action)
        )
        self._unsubs.append(self.coordinator.async_add_listener(self._on_coordinator_update))
        self._reschedule()

    async def async_on_started(self) -> None:
        """HA fully started: announce a restart we caused, clean stale runs."""
        # The coordinator's first refresh ran during setup, before HACS and
        # other integrations had created their update entities — refresh now
        # so "ready / waiting" (and AUM's other sensors) are right at once
        # instead of after the 30-minute poll.
        await self.coordinator.async_request_refresh()
        state = self.storage.get_autopilot_state()
        changed = False
        if state.get("restart_notice"):
            state["restart_notice"] = False
            changed = True
            await self._async_notify(AP_EVENT_RESTARTED, "restarted_title", "restarted_msg")
        if state.get("current_run") and not self.runner.is_running():
            # HA went down during the backup / before the runner took over.
            state["current_run"] = None
            if state.get("status") == AP_STATUS_RUNNING:
                state["status"] = AP_STATUS_FAILED
            changed = True
        if changed:
            await self.storage.async_save_autopilot_state(state)

    def async_unload(self) -> None:
        for unsub in self._unsubs + self._schedule_unsubs:
            unsub()
        self._unsubs.clear()
        self._schedule_unsubs.clear()

    # ---- Config ------------------------------------------------------------

    @property
    def config(self) -> dict:
        return self.storage.get_autopilot_config()

    async def async_save_config(self, new_config: dict) -> None:
        config = self.config
        config.update(new_config)
        # Saving = acknowledging every component installed right now, so only
        # things installed *after* this save count as "new".
        config["known_ids"] = {
            t: [c["id"] for c in self.list_components() if c["type"] == t]
            for t in (UPDATE_TYPE_ADDON, UPDATE_TYPE_HACS)
        }
        await self.storage.async_save_autopilot_config(config)
        state = self.storage.get_autopilot_state()
        state["notified_new"] = []
        await self.storage.async_save_autopilot_state(state)
        self._reschedule()

    async def async_set_enabled(self, enabled: bool) -> None:
        config = self.config
        config["enabled"] = enabled
        await self.storage.async_save_autopilot_config(config)
        self._reschedule()

    # ---- Schedule ------------------------------------------------------------

    @property
    def next_run(self) -> datetime | None:
        return self._next_run

    @property
    def is_running(self) -> bool:
        return self._busy or bool(self.storage.get_autopilot_state().get("current_run"))

    def _compute_next(self, after: datetime) -> datetime | None:
        config = self.config
        weekdays = set(config.get("weekdays") or [])
        if not weekdays:
            return None
        try:
            hour, minute = (int(x) for x in str(config.get("time", "03:00")).split(":")[:2])
        except ValueError:
            hour, minute = 3, 0
        # get_default_time_zone() arrived in HA 2024.6; older versions (AUM
        # supports 2024.1+) only have the DEFAULT_TIME_ZONE module attribute.
        get_tz = getattr(dt_util, "get_default_time_zone", None)
        tz = get_tz() if get_tz else dt_util.DEFAULT_TIME_ZONE
        local_after = dt_util.as_local(after)
        for offset in range(0, 8):
            day = local_after.date() + timedelta(days=offset)
            candidate = datetime.combine(day, time(hour, minute), tzinfo=tz)
            if candidate.weekday() in weekdays and candidate > local_after:
                return candidate
        return None

    def _reschedule(self) -> None:
        for unsub in self._schedule_unsubs:
            unsub()
        self._schedule_unsubs.clear()
        self._next_run = None

        config = self.config
        if config.get("enabled"):
            now = dt_util.now()
            nxt = self._compute_next(now)
            if nxt is not None:
                self._next_run = nxt
                self._schedule_unsubs.append(
                    async_track_point_in_time(self.hass, self._on_scheduled_run, nxt)
                )
                hours = int(config.get("prewarn_hours") or 0)
                if config["notify_events"].get(AP_EVENT_PREWARN) and hours > 0:
                    warn_at = nxt - timedelta(hours=hours)
                    if warn_at > now:
                        self._schedule_unsubs.append(
                            async_track_point_in_time(self.hass, self._on_prewarn, warn_at)
                        )
        self._signal()

    def _signal(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_AUTOPILOT_UPDATED)

    @callback
    def _on_scheduled_run(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_run(trigger="schedule"))
        self._reschedule()

    @callback
    def _on_prewarn(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_prewarn())

    async def _async_prewarn(self) -> None:
        state = self.storage.get_autopilot_state()
        if state.get("skip_next"):
            return
        ready = self.evaluate()["ready"]
        if not ready or self._next_run is None:
            return
        when = dt_util.as_local(self._next_run).strftime("%H:%M")
        await self._async_notify(
            AP_EVENT_PREWARN,
            "prewarn_title",
            "prewarn_msg",
            with_skip_action=True,
            time=when,
            count=len(ready),
            items=self._bullet([self._line(u["title"], u["from_version"], u["to_version"]) for u in ready]),
        )

    async def async_set_skip_next(self, skip: bool) -> None:
        state = self.storage.get_autopilot_state()
        state["skip_next"] = self._next_run.isoformat() if (skip and self._next_run) else None
        await self.storage.async_save_autopilot_state(state)

    async def _on_mobile_action(self, event: Event) -> None:
        if event.data.get("action") == AP_ACTION_SKIP:
            _LOGGER.info("AUM Autopilot: 'skip tonight' pressed on a mobile notification")
            await self.async_set_skip_next(True)

    # ---- Components & filters ------------------------------------------------

    def list_components(self) -> list[dict]:
        """Every installed add-on and HACS repo (pending or not), with the
        stable id selections are stored under (registry unique_id)."""
        from .websocket_api import _classify_entity  # noqa: PLC0415

        registry = er.async_get(self.hass)
        components = []
        for state in self.hass.states.async_all("update"):
            entry = registry.async_get(state.entity_id)
            update_type = _classify_entity(state.entity_id, entry)
            if update_type not in (UPDATE_TYPE_ADDON, UPDATE_TYPE_HACS):
                continue
            components.append({
                "id": (entry.unique_id if entry and entry.unique_id else state.entity_id),
                "entity_id": state.entity_id,
                "title": (
                    state.attributes.get("title")
                    or (entry and (entry.name or entry.original_name))
                    or state.attributes.get("friendly_name")
                    or state.entity_id
                ),
                "type": update_type,
                "pending": state.state == "on",
                "is_self": update_type == UPDATE_TYPE_HACS and is_self_update_entity(self.hass, state.entity_id, entry),
            })
        components.sort(key=lambda c: (c["type"], str(c["title"]).lower()))
        return components

    def new_unscheduled(self) -> list[dict]:
        """Components installed after the last save that "Only selected" mode
        therefore leaves out — the easy-to-forget case."""
        config = self.config
        known = config.get("known_ids") or {}
        result = []
        for comp in self.list_components():
            mode = config.get(f"{comp['type']}_mode", AP_MODE_OFF)
            if mode != AP_MODE_ONLY:
                continue
            if comp["id"] in (known.get(comp["type"]) or []):
                continue
            if comp["id"] in (config.get(f"{comp['type']}_selected") or []):
                continue
            result.append(comp)
        return result

    def evaluate(self, config: dict | None = None) -> dict:
        """Classify every pending Core/add-on/HACS update for the next run.

        `config` lets the panel preview unsaved settings with exactly the
        same rules a real run uses; None = the saved config.
        """
        if config is None:
            config = self.config
        registry = er.async_get(self.hass)
        first_seen = self.storage.get_autopilot_state().get("first_seen", {})
        min_age = int(config.get("min_age_days", 7))
        today = _today()

        ready, waiting, excluded = [], [], []
        for u in self.coordinator.data or []:
            update_type = u.get("type")
            if update_type not in AP_TYPES:
                continue
            entry = registry.async_get(u["entity_id"])
            comp_id = entry.unique_id if entry and entry.unique_id else u["entity_id"]
            released = _parse_date(u.get("release_date"))
            seen = _parse_date(first_seen.get(f"{u['entity_id']}|{u.get('latest_version', '')}"))
            age_from = released or seen or today
            item = {
                "entity_id": u["entity_id"],
                "id": comp_id,
                "title": u.get("title", u["entity_id"]),
                "type": update_type,
                "from_version": u.get("installed_version", ""),
                "to_version": u.get("latest_version", ""),
                "release_date": u.get("release_date") or "",
                "age_from": age_from.isoformat(),
                "age_days": (today - age_from).days,
                "major_version_change": bool(u.get("major_version_change")),
                "reason": None,
                "eligible_from": None,
            }

            reason = None
            if update_type == UPDATE_TYPE_CORE:
                if not config.get("core"):
                    reason = REASON_TYPE_OFF
            else:
                mode = config.get(f"{update_type}_mode", AP_MODE_OFF)
                selected = set(config.get(f"{update_type}_selected") or [])
                if mode == AP_MODE_OFF:
                    reason = REASON_TYPE_OFF
                elif mode == AP_MODE_ALL_EXCEPT and comp_id in selected:
                    reason = REASON_EXCLUDED
                elif mode == AP_MODE_ONLY and comp_id not in selected:
                    reason = REASON_NOT_SELECTED
                elif mode not in (AP_MODE_ALL, AP_MODE_ALL_EXCEPT, AP_MODE_ONLY):
                    reason = REASON_TYPE_OFF
            if reason is None and not u.get("installable", True):
                reason = REASON_NOT_INSTALLABLE
            if reason is None and u.get("in_progress"):
                reason = REASON_IN_PROGRESS
            if reason is None and item["major_version_change"] and not config.get("include_major"):
                reason = REASON_MAJOR
            if reason is None and item["age_days"] < min_age:
                reason = REASON_TOO_NEW
                item["eligible_from"] = (age_from + timedelta(days=min_age)).isoformat()

            item["reason"] = reason
            if reason is None:
                ready.append(item)
            elif reason == REASON_TOO_NEW:
                waiting.append(item)
            else:
                excluded.append(item)

        waiting.sort(key=lambda i: i["eligible_from"] or "")
        return {"ready": ready, "waiting": waiting, "excluded": excluded}

    @callback
    def _on_coordinator_update(self) -> None:
        self.hass.async_create_task(self._async_track_first_seen_and_new())

    async def _async_track_first_seen_and_new(self) -> None:
        state = self.storage.get_autopilot_state()
        changed = False

        # first_seen: age fallback for updates without a known release date.
        pending_keys = {
            f"{u['entity_id']}|{u.get('latest_version', '')}"
            for u in (self.coordinator.data or [])
        }
        first_seen = state.get("first_seen", {})
        for key in pending_keys:
            if key not in first_seen:
                first_seen[key] = _today().isoformat()
                changed = True
        for key in list(first_seen):
            if key not in pending_keys:
                del first_seen[key]
                changed = True
        state["first_seen"] = first_seen

        # New components left out by "Only selected" — notify once each.
        new = [c for c in self.new_unscheduled() if c["id"] not in state.get("notified_new", [])]
        if new:
            state["notified_new"] = list(state.get("notified_new", [])) + [c["id"] for c in new]
            changed = True
            await self._async_notify(
                AP_EVENT_NEW_COMPONENTS,
                "new_title",
                "new_msg",
                items=self._bullet([f"{self._name(c['title'])} ({c['type']})" for c in new]),
            )

        if changed:
            await self.storage.async_save_autopilot_state(state)
        else:
            self._signal()

    # ---- Run -------------------------------------------------------------------

    async def async_run(self, trigger: str = "manual") -> None:
        if self._busy:
            _LOGGER.warning("AUM Autopilot: a run is already being prepared, ignoring")
            return
        self._busy = True
        try:
            await self._async_run(trigger)
        finally:
            self._busy = False
            self._signal()

    async def _async_run(self, trigger: str) -> None:
        state = self.storage.get_autopilot_state()
        now = dt_util.now()

        if trigger == "schedule" and state.get("skip_next"):
            skip_at = dt_util.parse_datetime(state["skip_next"])
            if skip_at is None or skip_at <= now + timedelta(minutes=10):
                state["skip_next"] = None
                await self._async_finish_without_run(state, AP_STATUS_SKIPPED, trigger)
                await self._async_notify(AP_EVENT_SUMMARY, "skipped_title", "skipped_msg")
                return

        if self.runner.is_blocked() or self.runner.is_running():
            key = "blocked_code_red" if self.runner.is_blocked() else "blocked_busy"
            await self._async_finish_without_run(state, AP_STATUS_BLOCKED, trigger, error=key)
            await self._async_notify(AP_EVENT_UPDATE_FAILED, "blocked_title", key)
            return

        await self.coordinator.async_refresh()
        evaluation = self.evaluate()
        ready = evaluation["ready"]
        skipped_counts = {
            "waiting": len(evaluation["waiting"]),
            "major": sum(1 for i in evaluation["excluded"] if i["reason"] == REASON_MAJOR),
        }

        if not ready:
            await self._async_finish_without_run(state, AP_STATUS_NOTHING, trigger, extra=skipped_counts)
            await self._async_notify(
                AP_EVENT_NOTHING, "nothing_title", "nothing_msg", waiting=skipped_counts["waiting"],
            )
            return

        state["status"] = AP_STATUS_RUNNING
        state["current_run"] = {
            "trigger": trigger,
            "started_at": now.isoformat(),
            "planned": [
                {"entity_id": u["entity_id"], "title": u["title"], "type": u["type"],
                 "from_version": u["from_version"], "to_version": u["to_version"]}
                for u in ready
            ],
            **skipped_counts,
            "backup": None,
        }
        await self.storage.async_save_autopilot_state(state)
        await self._async_notify(
            AP_EVENT_START, "start_title", "start_msg",
            count=len(ready),
            items=self._bullet([self._line(u["title"], u["from_version"], u["to_version"]) for u in ready]),
        )

        ok, info = await async_create_verified_backup(self.hass)
        state = self.storage.get_autopilot_state()
        if not ok:
            _LOGGER.error("AUM Autopilot: backup failed, run aborted: %s", info)
            run = state.get("current_run") or {}
            state["current_run"] = None
            state["status"] = AP_STATUS_BACKUP_FAILED
            state["success_streak"] = 0
            state["last_run"] = {**run, "finished_at": dt_util.now().isoformat(),
                                 "status": AP_STATUS_BACKUP_FAILED, "error": info,
                                 "installed": [], "failed": [], "not_started": [u["title"] for u in ready]}
            await self.storage.async_save_autopilot_state(state)
            await self._async_notify(AP_EVENT_BACKUP_FAILED, "backup_failed_title", "backup_failed_msg", error=info)
            return

        state["last_backup"] = {"at": dt_util.now().isoformat(), "name": info}
        if state.get("current_run"):
            state["current_run"]["backup"] = info
        await self.storage.async_save_autopilot_state(state)
        await self._async_notify(AP_EVENT_BACKUP_OK, "backup_ok_title", "backup_ok_msg", name=info)

        started = await self.runner.async_start(
            [u["entity_id"] for u in ready],
            take_backup=False,
            origin=AP_ORIGIN,
            include_self=True,
            prior_backup=info,
        )
        if not started:
            state = self.storage.get_autopilot_state()
            await self._async_finish_without_run(state, AP_STATUS_BLOCKED, trigger, error="blocked_busy")
            await self._async_notify(AP_EVENT_UPDATE_FAILED, "blocked_title", "blocked_busy")

    async def _async_finish_without_run(
        self, state: dict, status: str, trigger: str, error: str | None = None, extra: dict | None = None,
    ) -> None:
        state["status"] = status
        state["current_run"] = None
        state["last_run"] = {
            "trigger": trigger,
            "started_at": dt_util.now().isoformat(),
            "finished_at": dt_util.now().isoformat(),
            "status": status,
            "error": error,
            "installed": [],
            "failed": [],
            "not_started": [],
            **(extra or {}),
        }
        if status == AP_STATUS_BLOCKED:
            state["success_streak"] = 0
        await self.storage.async_save_autopilot_state(state)
        self.hass.bus.async_fire(AP_BUS_EVENT, {"event": status, "trigger": trigger})

    async def _async_on_run_finished(self, fl_state: dict) -> None:
        if fl_state.get("origin") != AP_ORIGIN:
            return
        state = self.storage.get_autopilot_state()
        run = state.get("current_run") or {}
        steps = (fl_state.get("queue") or {}).get("steps", [])
        done = [s for s in steps if s.get("status") == FL_STEP_DONE]
        failed = [s for s in steps if s.get("status") in (FL_STEP_FAILED, FL_STEP_INSTALLING)]
        not_started = [s for s in steps if s not in done and s not in failed]
        success = fl_state.get("opcon") == FL_OPCON_OK

        core_done = any(s.get("phase") == FL_PHASE_CORE for s in done)
        hacs_done = any(s.get("phase") == FL_PHASE_HACS for s in done)
        restart = False
        if success and hacs_done and not core_done:
            restart = self._hacs_restart_needed([s for s in done if s.get("phase") == FL_PHASE_HACS])

        summary = {
            **run,
            "finished_at": dt_util.now().isoformat(),
            "status": AP_STATUS_SUCCESS if success else AP_STATUS_FAILED,
            "error": fl_state.get("last_error"),
            "installed": [{"entity_id": s["entity_id"], "title": s["title"], "phase": s["phase"]} for s in done],
            "failed": [{"entity_id": s["entity_id"], "title": s["title"], "error": s.get("error")} for s in failed],
            "not_started": [s["title"] for s in not_started],
            "core_restarted": core_done,
            "restarted": restart,
        }
        state["current_run"] = None
        state["last_run"] = summary
        state["status"] = summary["status"]
        state["success_streak"] = (state.get("success_streak", 0) + 1) if success else 0
        if restart:
            state["restart_notice"] = True
        await self.storage.async_save_autopilot_state(state)
        self.hass.bus.async_fire(AP_BUS_EVENT, {
            "event": summary["status"],
            "installed": [s["title"] for s in done],
            "failed": [s["title"] for s in failed],
        })

        t = self._t()
        if success:
            planned = {p["entity_id"]: p for p in run.get("planned", [])}
            lines = [self._bullet([
                self._line(
                    s["title"],
                    planned.get(s["entity_id"], {}).get("from_version"),
                    planned.get(s["entity_id"], {}).get("to_version"),
                )
                for s in done
            ])]
            if run.get("waiting"):
                lines.append(t["summary_waiting"].format(count=run["waiting"]))
            if run.get("major"):
                lines.append(t["summary_major"].format(count=run["major"]))
            if core_done:
                lines.append(t["summary_core"])
            if restart:
                lines.append(t["summary_restart"])
            await self._async_notify(
                AP_EVENT_SUMMARY, "summary_title", None, count=len(done),
                message_override=t["summary_msg"].format(items="\n".join(lines)),
            )
            if restart:
                _LOGGER.warning("AUM Autopilot: restarting Home Assistant to finish HACS updates")
                await self.hass.services.async_call("homeassistant", "restart", {}, blocking=False)
        else:
            await self._async_notify(
                AP_EVENT_UPDATE_FAILED, "failed_title", "failed_msg",
                error=fl_state.get("last_error") or "",
                done=", ".join(self._name(s["title"]) for s in done) or t["none"],
                not_started=", ".join(self._name(s["title"]) for s in not_started) or t["none"],
            )

    def _hacs_restart_needed(self, hacs_steps: list[dict]) -> bool:
        """Does finishing these HACS/marketplace updates need an HA restart?

        1. The classic HACS integration knows (repo.pending_restart).
        2. Otherwise (HA 2026.11+ marketplace) decide from each repository's
           category: only integrations need a restart — cards (plugins) and
           themes don't. Unknown category -> restart: an integration update
           left without a restart is the half-finished state Autopilot must
           never leave behind.
        """
        pending = _hacs_restart_pending(self.hass)
        if pending is not None:
            return pending
        registry = er.async_get(self.hass)
        for step in hacs_steps:
            kind = hacs_update_kind(self.hass, registry.async_get(step["entity_id"]))
            if kind is None or kind == "integration":
                return True
        return False

    # ---- Notifications -----------------------------------------------------------

    def _t(self) -> dict:
        lang = (self.hass.config.language or "en").split("-")[0]
        text = _TEXT.get(lang, _TEXT["en"])
        # HA 2026.11 ships HACS built in as "Marketplace" — use the name the
        # user actually sees in their Home Assistant.
        if (MAJOR_VERSION, MINOR_VERSION) >= (2026, 11):
            text = {k: v.replace("HACS", "Marketplace") for k, v in text.items()}
        return text

    @staticmethod
    def _bullet(items: list[str]) -> str:
        return "\n".join(f"• {i}" for i in items)

    @staticmethod
    def _name(title: str) -> str:
        """'Easy Floorplan update' -> 'Easy Floorplan' (HACS / entity names
        carry a trailing "update" that is just noise in a notification)."""
        title = str(title or "").strip()
        if title.lower().endswith(" update"):
            title = title[: -len(" update")].rstrip()
        return title

    @classmethod
    def _line(cls, title: str, from_version: str | None, to_version: str | None) -> str:
        if from_version and to_version:
            return f"{cls._name(title)} {from_version} → {to_version}"
        if to_version:
            return f"{cls._name(title)} → {to_version}"
        return cls._name(title)

    def available_targets(self) -> list[dict]:
        """Every place a notification can go, with a human name and a group.

        Target ids (what's stored in notify_targets):
          persistent_notification  -> HA's own notification bell
          notify.<service>         -> legacy notify service (mobile app etc.)
          entity:notify.<object>   -> new-style notify entity (send_message)
        """
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415
        from homeassistant.util import slugify  # noqa: PLC0415

        t = self._t()
        targets: list[dict] = [
            {"id": AP_TARGET_PERSISTENT, "name": t["target_persistent"], "group": "builtin"},
        ]

        # Mobile app services are named mobile_app_<slugified device name>;
        # map them back to the name the user actually knows.
        phone_names: dict[str, str] = {}
        try:
            registry = dr.async_get(self.hass)
            for entry in self.hass.config_entries.async_entries("mobile_app"):
                for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
                    if device.name:
                        phone_names[f"mobile_app_{slugify(device.name)}"] = device.name_by_user or device.name
        except Exception:  # noqa: BLE001
            pass

        services = self.hass.services.async_services().get("notify", {})
        for name in sorted(services):
            if name in ("send_message", "persistent_notification"):
                continue
            if name.startswith("mobile_app_"):
                pretty = phone_names.get(name) or name.removeprefix("mobile_app_").replace("_", " ").title()
                targets.append({"id": f"notify.{name}", "name": pretty, "group": "mobile"})
            elif name == "notify":
                targets.append({"id": "notify.notify", "name": "notify.notify", "group": "advanced"})
            else:
                targets.append({"id": f"notify.{name}", "name": name.replace("_", " ").title(), "group": "other"})

        for state in sorted(self.hass.states.async_all("notify"), key=lambda st: st.entity_id):
            targets.append({
                "id": f"entity:{state.entity_id}",
                "name": state.attributes.get("friendly_name") or state.entity_id,
                "group": "other",
            })
        return targets

    async def _async_send_to(
        self, target: str, title: str, message: str, *, event: str, with_skip_action: bool = False,
    ) -> str | None:
        """Deliver one notification. Returns None on success, else the error."""
        try:
            if target == AP_TARGET_PERSISTENT:
                await self.hass.services.async_call(
                    "persistent_notification", "create",
                    {"title": title, "message": message, "notification_id": f"aum_autopilot_{event}"},
                    blocking=True,
                )
                return None
            if target.startswith("entity:"):
                entity_id = target.removeprefix("entity:")
                if self.hass.states.get(entity_id) is None:
                    return f"{entity_id} no longer exists"
                await self.hass.services.async_call(
                    "notify", "send_message",
                    {"entity_id": entity_id, "title": title, "message": message},
                    blocking=True,
                )
                return None
            service = target.removeprefix("notify.")
            if not self.hass.services.has_service("notify", service):
                return f"{target} no longer exists"
            payload: dict = {"title": title, "message": message}
            if with_skip_action and service.startswith("mobile_app_"):
                payload["data"] = {"actions": [{"action": AP_ACTION_SKIP, "title": self._t()["skip_action"]}]}
            await self.hass.services.async_call("notify", service, payload, blocking=True)
            return None
        except Exception as err:  # noqa: BLE001
            return str(err) or err.__class__.__name__

    def _instance_name(self) -> str:
        return (self.hass.config.location_name or "Home Assistant").strip()

    def _brand(self, title: str, message: str) -> tuple[str, str]:
        """Make the sender unmistakable — people may run several HA instances
        and get many notifications. Instance name goes into the title (seen
        on the lock screen) and a sender line opens the message."""
        instance = self._instance_name()
        if title.startswith("Autopilot"):
            title = f"Autopilot ({instance}){title[len('Autopilot'):]}"
        else:
            title = f"Autopilot ({instance}): {title}"
        sender = self._t()["sender"].format(instance=instance)
        return title, f"{sender}\n\n{message}"

    async def async_send_test(self, targets: list[str]) -> list[dict]:
        """"Send test notification" in the panel — works on unsaved targets."""
        t = self._t()
        title, message = self._brand(t["test_title"], t["test_msg"])
        results = []
        for target in targets:
            error = await self._async_send_to(target, title, message, event="test")
            results.append({"target": target, "ok": error is None, "error": error})
        return results

    async def _async_notify(
        self,
        event: str,
        title_key: str,
        message_key: str | None,
        *,
        with_skip_action: bool = False,
        message_override: str | None = None,
        **fmt,
    ) -> None:
        t = self._t()
        title = t[title_key].format(**fmt)
        message = message_override if message_override is not None else t.get(message_key, message_key).format(**fmt)

        # The bus event keeps the plain texts (plus the instance name) so
        # people's own automations can format them however they like.
        self.hass.bus.async_fire(AP_BUS_EVENT, {
            "event": event, "title": title, "message": message, "instance": self._instance_name(),
        })

        config = self.config
        if event not in AP_EVENTS_LOCKED_ON and not config.get("notify_events", {}).get(event):
            return
        title, message = self._brand(title, message)
        for target in config.get("notify_targets") or []:
            error = await self._async_send_to(
                target, title, message, event=event, with_skip_action=with_skip_action,
            )
            if error:
                _LOGGER.warning("AUM Autopilot: notification via %s failed: %s", target, error)

    # ---- Snapshot for websocket / sensors ------------------------------------------

    def snapshot(self) -> dict:
        state = self.storage.get_autopilot_state()
        state.pop("first_seen", None)
        state.pop("notified_new", None)
        return {
            "config": self.config,
            "state": state,
            "next_run": self._next_run.isoformat() if self._next_run else None,
            "running": self.is_running,
            "opcon": self.storage.get_feeling_lucky_state().get("opcon"),
            "evaluation": self.evaluate(),
            "new_unscheduled": self.new_unscheduled(),
        }


def sanitize_config(raw: dict) -> dict:
    """Clamp/validate a config coming from the panel."""
    from .const import AP_DEFAULT_CONFIG, AP_EVENTS_DEFAULT, AP_MIN_AGE_MAX, AP_MODES  # noqa: PLC0415

    cfg = copy.deepcopy(AP_DEFAULT_CONFIG)
    cfg.pop("known_ids", None)
    for key in ("enabled", "core", "include_major"):
        if key in raw:
            cfg[key] = bool(raw[key])
    time_str = str(raw.get("time", cfg["time"]))
    try:
        hh, mm = (int(x) for x in time_str.split(":")[:2])
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
        cfg["time"] = f"{hh:02d}:{mm:02d}"
    except ValueError:
        pass
    cfg["weekdays"] = sorted({int(d) for d in raw.get("weekdays", cfg["weekdays"]) if 0 <= int(d) <= 6})
    for t in ("addon", "hacs"):
        mode = raw.get(f"{t}_mode", AP_MODE_OFF)
        cfg[f"{t}_mode"] = mode if mode in AP_MODES else AP_MODE_OFF
        cfg[f"{t}_selected"] = [str(x) for x in raw.get(f"{t}_selected", [])]
    try:
        cfg["min_age_days"] = max(0, min(AP_MIN_AGE_MAX, int(raw.get("min_age_days", cfg["min_age_days"]))))
    except (TypeError, ValueError):
        pass
    try:
        cfg["prewarn_hours"] = max(1, min(24, int(raw.get("prewarn_hours", cfg["prewarn_hours"]))))
    except (TypeError, ValueError):
        pass
    cfg["notify_targets"] = [str(x) for x in raw.get("notify_targets", cfg["notify_targets"])]
    events = {**AP_EVENTS_DEFAULT, **{k: bool(v) for k, v in (raw.get("notify_events") or {}).items() if k in AP_EVENTS_DEFAULT}}
    for locked in AP_EVENTS_LOCKED_ON:
        events[locked] = True
    cfg["notify_events"] = events
    return cfg
