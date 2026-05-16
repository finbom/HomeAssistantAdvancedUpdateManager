"""Constants for Advanced Update Manager."""

DOMAIN = "advanced_update_manager"

PANEL_TITLE = "Update Manager"
PANEL_ICON = "mdi:update"
PANEL_URL_PATH = "advanced-update-manager"
PANEL_COMPONENT = "advanced-update-manager-panel"
PANEL_JS = "advanced-update-manager-panel.js"

STORAGE_KEY = "advanced_update_manager"
STORAGE_VERSION = 1

UPDATE_TYPE_CORE = "core"
UPDATE_TYPE_HAOS = "haos"
UPDATE_TYPE_ADDON = "addon"
UPDATE_TYPE_HACS = "hacs"
UPDATE_TYPE_DEVICE = "device"
UPDATE_TYPE_OTHER = "other"

CORE_ENTITY_ID = "update.home_assistant_core_update"
HAOS_ENTITY_ID = "update.home_assistant_operating_system_update"

CONF_SHOW_IN_SIDEBAR = "show_in_sidebar"
