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
