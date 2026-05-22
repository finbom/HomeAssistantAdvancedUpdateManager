"""Config flow for Advanced Update Manager."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .const import (
    BACKUP_TYPE_FULL,
    BACKUP_TYPE_ADDON_ONLY,
    CONF_DEFAULT_BACKUP_TYPE,
    CONF_SHOW_IN_SIDEBAR,
    DOMAIN,
)


class AdvancedUpdateManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="Advanced Update Manager", data={})

        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        show_in_sidebar = self._config_entry.options.get(CONF_SHOW_IN_SIDEBAR, True)
        default_backup_type = self._config_entry.options.get(CONF_DEFAULT_BACKUP_TYPE, BACKUP_TYPE_FULL)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_SHOW_IN_SIDEBAR, default=show_in_sidebar): bool,
                vol.Required(CONF_DEFAULT_BACKUP_TYPE, default=default_backup_type): SelectSelector(
                    SelectSelectorConfig(
                        options=[BACKUP_TYPE_FULL, BACKUP_TYPE_ADDON_ONLY],
                        translation_key=CONF_DEFAULT_BACKUP_TYPE,
                    )
                ),
            }),
        )
