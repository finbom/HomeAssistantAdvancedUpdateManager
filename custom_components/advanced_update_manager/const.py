"""Constants for Advanced Update Manager."""

DOMAIN = "advanced_update_manager"

PANEL_TITLE = "Update Manager"
PANEL_ICON = "mdi:update"
PANEL_URL_PATH = "advanced-update-manager"
PANEL_COMPONENT = "advanced-update-manager-panel"
PANEL_JS = "advanced-update-manager-panel.js"

STORAGE_KEY = "advanced_update_manager"
STORAGE_VERSION = 1

UPDATE_TYPE_SUPERVISOR = "supervisor"
UPDATE_TYPE_CORE = "core"
UPDATE_TYPE_HAOS = "haos"
UPDATE_TYPE_ADDON = "addon"
UPDATE_TYPE_HACS = "hacs"
UPDATE_TYPE_DEVICE = "device"
UPDATE_TYPE_OTHER = "other"

# Entity-registry platforms whose update entities are "HACS" updates. Home
# Assistant 2026.11 ships HACS built in as the "marketplace" integration and
# migrates the entities to it; the custom "hacs" integration is the old home.
HACS_PLATFORMS = ("hacs", "marketplace")

CORE_ENTITY_ID = "update.home_assistant_core_update"
HAOS_ENTITY_ID = "update.home_assistant_operating_system_update"
SUPERVISOR_ENTITY_ID = "update.home_assistant_supervisor_update"

CONF_SHOW_IN_SIDEBAR = "show_in_sidebar"
CONF_DEFAULT_BACKUP_TYPE = "default_backup_type"
CONF_HISTORY_KEEP_DAYS = "history_keep_days"
HISTORY_KEEP_DAYS_DEFAULT = 365

# Default-off gate for the beta Feeling Lucky feature — see
# project-safe-feature-rollout memory for why (risky, restart/reboot-heavy
# functionality shouldn't appear for existing HACS users until they opt in).
CONF_ENABLE_FEELING_LUCKY = "enable_feeling_lucky"

BACKUP_TYPE_FULL = "full"
BACKUP_TYPE_ADDON_ONLY = "addon_only"

# ── Feeling Lucky ─────────────────────────────────────────────────────────
# Hardcoded phase order. Supervisor first is a hard platform requirement — HA
# itself gates every other update on it. Everything else runs lowest-risk /
# most-reversible first, saving the hardest-to-reverse, restart-heavy steps
# (OS, then Core) for last, once HACS/add-ons/firmware are already confirmed
# healthy on the current Core/OS. AUM's own update entity is always excluded
# from the HACS phase — it must not update the code that is running the
# sequence.
FL_PHASE_SUPERVISOR = "supervisor"
FL_PHASE_HACS = "hacs"
FL_PHASE_ADDON = "addon"
FL_PHASE_DEVICE = "device"
FL_PHASE_HAOS = "haos"
FL_PHASE_CORE = "core"

FL_PHASE_ORDER = [
    FL_PHASE_SUPERVISOR,
    FL_PHASE_HACS,
    FL_PHASE_ADDON,
    FL_PHASE_DEVICE,
    FL_PHASE_HAOS,
    FL_PHASE_CORE,
]

FL_TYPE_TO_PHASE = {
    UPDATE_TYPE_SUPERVISOR: FL_PHASE_SUPERVISOR,
    UPDATE_TYPE_HAOS: FL_PHASE_HAOS,
    UPDATE_TYPE_CORE: FL_PHASE_CORE,
    UPDATE_TYPE_ADDON: FL_PHASE_ADDON,
    UPDATE_TYPE_HACS: FL_PHASE_HACS,
    UPDATE_TYPE_DEVICE: FL_PHASE_DEVICE,
}

# Per-category timeout (seconds) waiting for a step to report done after
# install is triggered. Deliberately per-category, not a single shared value:
# an OS update reboots the whole host, a device firmware flash can take
# several minutes and multiple reboots, an add-on restart is comparatively
# quick. Bias is toward being over-sensitive (shorter) rather than lax.
FL_TIMEOUT_BY_PHASE = {
    FL_PHASE_SUPERVISOR: 180,
    FL_PHASE_HAOS: 900,
    FL_PHASE_CORE: 180,
    FL_PHASE_ADDON: 120,
    FL_PHASE_HACS: 120,
    FL_PHASE_DEVICE: 600,
}
FL_TIMEOUT_DEFAULT = 120
FL_POLL_INTERVAL = 5

FL_OPCON_OK = "ok"
FL_OPCON_RUNNING = "running"
FL_OPCON_CRITICAL = "critical"

# Repairs issue raised on Code Red, cleared by reset_opcon — lives alongside
# OPCON's own lifecycle rather than being independently dismissible, so the
# two can't drift out of sync (see _async_freeze / async_reset_opcon).
FL_REPAIR_ISSUE_ID = "feeling_lucky_code_red"

FL_STEP_PENDING = "pending"
FL_STEP_INSTALLING = "installing"
FL_STEP_DONE = "done"
FL_STEP_FAILED = "failed"
FL_STEP_NEVER_STARTED = "never_started"

# Substring match against a HACS update entity's unique_id (HACS sets this to
# the repository's "owner/repo" full name) to exclude AUM's own update from
# the HACS phase — see FL_PHASE_ORDER note above.
FL_SELF_REPO_MARKER = "homeassistantadvancedupdatemanager"

SERVICE_START_FEELING_LUCKY = "start_feeling_lucky"
SERVICE_RESET_OPCON = "reset_opcon"

# Dispatcher signal fired whenever the Feeling Lucky OPCON/queue state is
# saved, so the two status sensors can push an update instantly instead of
# waiting for their next poll.
SIGNAL_FEELING_LUCKY_UPDATED = f"{DOMAIN}_feeling_lucky_updated"

# ── Autopilot ─────────────────────────────────────────────────────────────
# Scheduled, unattended update runs. Autopilot decides *what* and *when*
# (schedule + filters) and hands the actual sequence to FeelingLuckyRunner,
# so ordering, persistence across restarts, timeouts and Code Red all behave
# exactly like a Feeling Lucky run. Only Core, add-ons (apps) and HACS are
# supported — everything else is never touched by Autopilot.
AP_TYPES = [UPDATE_TYPE_CORE, UPDATE_TYPE_ADDON, UPDATE_TYPE_HACS]

AP_MODE_OFF = "off"
AP_MODE_ALL = "all"
AP_MODE_ALL_EXCEPT = "all_except"
AP_MODE_ONLY = "only"
AP_MODES = [AP_MODE_OFF, AP_MODE_ALL, AP_MODE_ALL_EXCEPT, AP_MODE_ONLY]

AP_MIN_AGE_DEFAULT = 7
AP_MIN_AGE_MAX = 360

AP_ORIGIN = "autopilot"

# Run outcome, exposed as the Autopilot Status sensor's state.
AP_STATUS_NEVER = "never_run"
AP_STATUS_RUNNING = "running"
AP_STATUS_SUCCESS = "success"
AP_STATUS_FAILED = "failed"
AP_STATUS_BACKUP_FAILED = "backup_failed"
AP_STATUS_NOTHING = "nothing_to_do"
AP_STATUS_SKIPPED = "skipped"
AP_STATUS_BLOCKED = "blocked"

# Notification events the user can opt in/out of. backup_failed is locked on
# ("Autopilot disengaged" must never be silent).
AP_EVENT_PREWARN = "prewarn"
AP_EVENT_START = "start"
AP_EVENT_BACKUP_OK = "backup_ok"
AP_EVENT_BACKUP_FAILED = "backup_failed"
AP_EVENT_UPDATE_FAILED = "update_failed"
AP_EVENT_SUMMARY = "summary"
AP_EVENT_RESTARTED = "restarted"
AP_EVENT_NOTHING = "nothing_to_do"
AP_EVENT_NEW_COMPONENTS = "new_components"
AP_EVENTS_DEFAULT = {
    AP_EVENT_PREWARN: False,
    AP_EVENT_START: False,
    AP_EVENT_BACKUP_OK: False,
    AP_EVENT_BACKUP_FAILED: True,
    AP_EVENT_UPDATE_FAILED: True,
    AP_EVENT_SUMMARY: True,
    AP_EVENT_RESTARTED: True,
    AP_EVENT_NOTHING: False,
    AP_EVENT_NEW_COMPONENTS: True,
}
AP_EVENTS_LOCKED_ON = {AP_EVENT_BACKUP_FAILED}

# Pseudo notify target meaning "HA's own notification bell".
AP_TARGET_PERSISTENT = "persistent_notification"

# Action id on the mobile-app pre-warning notification ("Skip tonight").
AP_ACTION_SKIP = "AUM_AUTOPILOT_SKIP"

# Fired on the HA event bus for every Autopilot milestone, so users can build
# their own automations regardless of which notifications they enabled.
AP_BUS_EVENT = f"{DOMAIN}_autopilot"

# How long to wait for a full backup to show up before calling it failed.
AP_BACKUP_TIMEOUT = 3600

SERVICE_AUTOPILOT_RUN_NOW = "autopilot_run_now"
SERVICE_AUTOPILOT_SKIP_NEXT = "autopilot_skip_next"

SIGNAL_AUTOPILOT_UPDATED = f"{DOMAIN}_autopilot_updated"

AP_DEFAULT_CONFIG = {
    "enabled": False,
    "time": "03:00",
    "weekdays": [0, 1, 2, 3, 4, 5, 6],  # Monday = 0
    "core": False,
    "addon_mode": AP_MODE_OFF,
    "addon_selected": [],
    "hacs_mode": AP_MODE_OFF,
    "hacs_selected": [],
    "include_major": False,
    "min_age_days": AP_MIN_AGE_DEFAULT,
    "notify_targets": [AP_TARGET_PERSISTENT],
    "notify_events": dict(AP_EVENTS_DEFAULT),
    "prewarn_hours": 2,
    # Components that existed when the user last saved — anything installed
    # later is "new" and, in "only selected" mode, not part of the schedule.
    "known_ids": {"addon": [], "hacs": []},
}
