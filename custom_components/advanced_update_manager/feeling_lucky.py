"""Feeling Lucky — an orchestrated, ordered update sequence.

Design summary (see project memory for the full history of this decision):
- Hardcoded phase order: Supervisor -> HACS -> Add-ons -> Device firmware ->
  OS -> Core. Supervisor-first is a hard platform requirement; everything
  else runs lowest-risk/most-reversible first, saving the restart-heavy OS
  and Core steps for last. AUM's own update entity is always excluded from
  the HACS phase.
- "Blue Screen" failure model: any critical error freezes the whole run
  immediately. Stop != rollback. The frozen queue snapshot is left in place
  (not cleared) so it can be used for troubleshooting.
- OPCON going critical blocks starting any *new* run. The only way back to
  green is the explicit reset action, which clears OPCON and the queue
  snapshot together.
- Persistence happens BEFORE each step is triggered (not after), and a run
  resumes on the `homeassistant_started` event, because a Core/OS install in
  this same sequence will restart Home Assistant itself mid-run.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    FL_OPCON_CRITICAL,
    FL_OPCON_OK,
    FL_OPCON_RUNNING,
    FL_PHASE_HAOS,
    FL_PHASE_ORDER,
    FL_POLL_INTERVAL,
    FL_REPAIR_ISSUE_ID,
    FL_SELF_REPO_MARKER,
    FL_STEP_DONE,
    FL_STEP_FAILED,
    FL_STEP_INSTALLING,
    FL_STEP_PENDING,
    FL_TIMEOUT_BY_PHASE,
    FL_TIMEOUT_DEFAULT,
    FL_TYPE_TO_PHASE,
)
from .storage import UpdateDateStorage

_LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _async_reboot_required(hass: HomeAssistant) -> bool:
    """True if Supervisor has an active (undismissed) "reboot required" repair.

    Supervisor mirrors its resolution-center issues into HA's own issue
    registry under the "hassio" domain. Matched by substring on "reboot"
    rather than an exact issue_id, since that's more robust to small naming
    differences across Supervisor versions than hardcoding one exact string.
    Shared with websocket_api.ws_get_restart_info so the panel's restart
    banner and Feeling Lucky agree on what "a reboot is needed" means.
    """
    try:
        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415
        registry = ir.async_get(hass)
        for issue in registry.issues.values():
            if (
                issue.domain == "hassio"
                and "reboot" in issue.issue_id.lower()
                and issue.dismissed_version is None
            ):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


class FeelingLuckyRunner:
    """Owns the single Feeling Lucky run for one config entry."""

    def __init__(self, hass: HomeAssistant, storage: UpdateDateStorage, enabled: bool = False) -> None:
        self.hass = hass
        self.storage = storage
        self.enabled = enabled
        self._task: asyncio.Task | None = None

    # ---- Public API ----------------------------------------------------

    def is_blocked(self) -> bool:
        """OPCON critical blocks starting any new run."""
        return self.storage.get_feeling_lucky_state().get("opcon") == FL_OPCON_CRITICAL

    async def async_start(self, entity_ids: list[str], take_backup: bool) -> None:
        if not self.enabled:
            # Checked here, not just hidden in the frontend, so a direct
            # service call (Developer Tools, an automation) can't bypass the
            # default-off gate either — see CONF_ENABLE_FEELING_LUCKY.
            _LOGGER.warning(
                "AUM Feeling Lucky: refused to start — the feature is disabled "
                "in this integration's options (off by default)"
            )
            return
        if self.is_blocked():
            _LOGGER.warning(
                "AUM Feeling Lucky: refused to start a new run — OPCON is Code Red, "
                "reset it first"
            )
            return
        if self._task is not None and not self._task.done():
            _LOGGER.warning("AUM Feeling Lucky: a run is already in progress, ignoring")
            return

        steps = self._build_steps(entity_ids)
        if not steps:
            _LOGGER.warning(
                "AUM Feeling Lucky: none of the requested entities are eligible "
                "pending updates, nothing to do"
            )
            return

        state = {
            "opcon": FL_OPCON_RUNNING,
            "last_error": None,
            "since": _now_iso(),
            "backup_taken": None,
            "queue": {
                "steps": steps,
                "current_item": None,
                "started_at": _now_iso(),
            },
        }
        await self.storage.async_save_feeling_lucky_state(state)

        if take_backup:
            if not await self._async_take_backup(state):
                return  # already frozen to Code Red inside _async_take_backup

        self._task = self.hass.async_create_task(self._async_run(state))

    async def async_resume_if_needed(self) -> None:
        """Call on `homeassistant_started`. Picks a run back up if HA died
        mid-sequence — expected when the step it was waiting on was a
        Core/OS install that restarts HA itself."""
        state = self.storage.get_feeling_lucky_state()
        if state.get("opcon") != FL_OPCON_RUNNING:
            return
        _LOGGER.info("AUM Feeling Lucky: resuming an in-progress run after restart")
        self._task = self.hass.async_create_task(self._async_run(state, resuming=True))

    async def async_reset_opcon(self) -> None:
        """The only way back to green — clears OPCON and the queue together."""
        await self.storage.async_reset_feeling_lucky_state()
        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415
        ir.async_delete_issue(self.hass, DOMAIN, FL_REPAIR_ISSUE_ID)
        _LOGGER.info("AUM Feeling Lucky: OPCON reset to ok")

    # ---- Internals -------------------------------------------------------

    def _build_steps(self, entity_ids: list[str]) -> list[dict]:
        registry = er.async_get(self.hass)
        coordinator = self.hass.data[DOMAIN]["coordinator"]
        by_id = {u["entity_id"]: u for u in (coordinator.data or [])}

        steps = []
        for entity_id in entity_ids:
            update = by_id.get(entity_id)
            if not update:
                continue  # not currently a pending update, skip silently

            phase = FL_TYPE_TO_PHASE.get(update.get("type"))
            if not phase:
                continue  # UPDATE_TYPE_OTHER isn't part of any Feeling Lucky phase

            if phase == "hacs":
                entry = registry.async_get(entity_id)
                uid = (entry.unique_id if entry else "") or ""
                if FL_SELF_REPO_MARKER in uid.lower():
                    continue  # never let the queue update the code running it

            steps.append({
                "entity_id": entity_id,
                "title": update.get("title", entity_id),
                "phase": phase,
                "status": FL_STEP_PENDING,
                "error": None,
            })

        order = {phase: i for i, phase in enumerate(FL_PHASE_ORDER)}
        steps.sort(key=lambda s: order.get(s["phase"], len(FL_PHASE_ORDER)))
        return steps

    async def _async_take_backup(self, state: dict) -> bool:
        try:
            if self.hass.services.has_service("backup", "create"):
                await self.hass.services.async_call("backup", "create", {}, blocking=True)
            state["backup_taken"] = True
            await self.storage.async_save_feeling_lucky_state(state)
            return True
        except Exception as err:  # noqa: BLE001
            state["backup_taken"] = False
            await self._async_freeze(state, f"Backup failed before the run could start: {err}")
            return False

    async def _async_run(self, state: dict, resuming: bool = False) -> None:
        steps = state["queue"]["steps"]

        start_index = len(steps)
        for i, step in enumerate(steps):
            if step["status"] != FL_STEP_DONE:
                start_index = i
                break

        for index in range(start_index, len(steps)):
            step = steps[index]
            is_resumed_step = resuming and index == start_index and step["status"] == FL_STEP_INSTALLING

            state["queue"]["current_item"] = step["entity_id"]
            step["status"] = FL_STEP_INSTALLING
            if not is_resumed_step:
                # Baseline for _async_wait_for_install_done — captured once,
                # before install() is triggered, and persisted so a resumed
                # run after a restart still compares against the original
                # pre-install value rather than whatever's current by then.
                step["installed_before"] = self._get_installed_version(step["entity_id"])
            # Persist BEFORE triggering install — this is what lets a run
            # survive HA dying mid-step (a Core/OS install restarting itself).
            await self.storage.async_save_feeling_lucky_state(state)

            ok, error = await self._async_run_step(step, is_resumed_step)
            if not ok:
                step["status"] = FL_STEP_FAILED
                step["error"] = error
                await self._async_freeze(state, error)
                return

            step["status"] = FL_STEP_DONE
            await self.storage.async_save_feeling_lucky_state(state)

        state["opcon"] = FL_OPCON_OK
        state["queue"]["current_item"] = None
        await self.storage.async_save_feeling_lucky_state(state)
        _LOGGER.info("AUM Feeling Lucky: run completed successfully")

    def _get_installed_version(self, entity_id: str) -> str | None:
        entity_state = self.hass.states.get(entity_id)
        if entity_state is None:
            return None
        return entity_state.attributes.get("installed_version")

    async def _async_run_step(self, step: dict, is_resumed_step: bool) -> tuple[bool, str | None]:
        entity_id = step["entity_id"]
        timeout = FL_TIMEOUT_BY_PHASE.get(step["phase"], FL_TIMEOUT_DEFAULT)
        installed_before = step.get("installed_before")

        if not is_resumed_step:
            try:
                # blocking=True mainly lets a well-behaved entity's install()
                # finish (or fail) before we start polling, saving a poll
                # cycle in the common case. It does NOT make exceptions from
                # inside async_install reach this try/except — HA's entity
                # service dispatch (entity_platform.py) catches and logs
                # those per-entity internally regardless of blocking. Do not
                # rely on this except-block for anything but dispatch-level
                # errors (bad entity_id, service not found).
                await self.hass.services.async_call(
                    "update", "install", {"entity_id": entity_id}, blocking=True,
                )
            except Exception as err:  # noqa: BLE001
                return False, f"{entity_id}: install call failed: {err}"

        # The timeout below is the ONE reliable failure signal across
        # arbitrary update entities — there's no generic way to detect "it
        # failed" faster than "it didn't finish in time" without risking
        # false positives on entities that legitimately keep working in the
        # background (Core/OS/Supervisor installs restart HA and can't wait
        # for themselves). That's exactly why timeouts are per-category
        # instead of one shared value. A HA restart may happen between any
        # two polls here — that's fine, async_resume_if_needed() re-enters
        # this same wait after restart.
        if not await self._async_wait_for_install_done(entity_id, installed_before, timeout):
            return False, f"{entity_id}: timed out after {timeout}s waiting for the update to complete"

        if step["phase"] == FL_PHASE_HAOS:
            # Supervisor no longer auto-reboots after a HAOS update (by
            # design, since ~2024 — see home-assistant/supervisor#6978): the
            # update entity reports "off" once the new OS image is staged,
            # NOT once it's actually active. It raises a "reboot required"
            # repair instead and waits for an explicit trigger. Skipping this
            # would mark the OS phase done while the update isn't really
            # applied yet — exactly the kind of half-finished state Blue
            # Screen exists to prevent moving past.
            ok, error = await self._async_ensure_host_rebooted(entity_id, timeout)
            if not ok:
                return False, error

        return True, None

    async def _async_wait_for_install_done(
        self, entity_id: str, installed_before: str | None, timeout: int
    ) -> bool:
        # Checking state == "off" alone isn't reliable: on a Supervisor-backed
        # update entity, a successful install and a brand-new release showing
        # up can land in the very same refresh — installed_version jumps to
        # the version we asked for, but latest_version jumps past it in the
        # same beat, so the entity goes straight from "on" to "on" and never
        # passes through "off" at all. Confirmed for real via recorder history
        # during testing: installed_version 6.0.0->6.1.0 and latest_version
        # 6.1.0->6.3.0 landed in the same state-changed event. Comparing
        # installed_version against its pre-install baseline catches this;
        # state == "off" is kept too for entities that behave the "normal" way.
        elapsed = 0
        while elapsed < timeout:
            entity_state = self.hass.states.get(entity_id)
            if entity_state is not None:
                if entity_state.state == "off":
                    return True
                if installed_before is not None:
                    installed_now = entity_state.attributes.get("installed_version")
                    if installed_now is not None and installed_now != installed_before:
                        return True
            await asyncio.sleep(FL_POLL_INTERVAL)
            elapsed += FL_POLL_INTERVAL
        return False

    async def _async_ensure_host_rebooted(self, entity_id: str, timeout: int) -> tuple[bool, str | None]:
        # Supervisor doesn't raise the "reboot required" issue the instant
        # the entity reports "off" — there's a short real-world gap. Give it
        # a grace window rather than checking once and concluding "no reboot
        # needed" just because we happened to look too early.
        if not await self._async_wait_briefly_for_reboot_required():
            _LOGGER.warning(
                "AUM Feeling Lucky: HAOS phase — no 'reboot required' repair appeared "
                "within the grace window for %s, treating the OS phase as complete",
                entity_id,
            )
            return True, None

        _LOGGER.warning(
            "AUM Feeling Lucky: HAOS phase — 'reboot required' detected for %s, "
            "calling hassio.host_reboot now",
            entity_id,
        )
        try:
            await self.hass.services.async_call("hassio", "host_reboot", {}, blocking=False)
        except Exception as err:  # noqa: BLE001
            return False, f"{entity_id}: failed to trigger the required host reboot: {err}"
        _LOGGER.warning("AUM Feeling Lucky: hassio.host_reboot call returned, now waiting for it to take effect")

        # The host reboot kills this coroutine mid-wait, same as any other
        # Core/OS/Supervisor restart — async_resume_if_needed() re-enters
        # _async_run_step for this same step afterward. Since the update
        # entity is already "off" by this point, is_resumed_step routes
        # straight back here and re-checks _async_reboot_required(), which
        # will now correctly read False once the reboot has actually happened.
        elapsed = 0
        while elapsed < timeout:
            if not await _async_reboot_required(self.hass):
                _LOGGER.warning(
                    "AUM Feeling Lucky: HAOS phase — 'reboot required' cleared for %s, phase complete",
                    entity_id,
                )
                return True, None
            await asyncio.sleep(FL_POLL_INTERVAL)
            elapsed += FL_POLL_INTERVAL
        return False, f"{entity_id}: host reboot did not clear the 'reboot required' flag within {timeout}s"

    async def _async_wait_briefly_for_reboot_required(self, grace_seconds: int = 120) -> bool:
        elapsed = 0
        while elapsed < grace_seconds:
            if await _async_reboot_required(self.hass):
                return True
            await asyncio.sleep(FL_POLL_INTERVAL)
            elapsed += FL_POLL_INTERVAL
        return False

    async def _async_freeze(self, state: dict, error: str) -> None:
        """Blue Screen: freeze everything, leave the queue in place, block new runs."""
        state["opcon"] = FL_OPCON_CRITICAL
        state["last_error"] = error
        state["queue"]["current_item"] = None
        await self.storage.async_save_feeling_lucky_state(state)
        _LOGGER.error("AUM Feeling Lucky: Code Red — %s", error)

        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            FL_REPAIR_ISSUE_ID,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="feeling_lucky_code_red",
            translation_placeholders={"error": error},
        )
