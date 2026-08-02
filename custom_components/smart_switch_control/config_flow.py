from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ACTION_ENTITY,
    CONF_BUTTON1_ENTITY,
    CONF_BUTTON2_ENTITY,
    CONF_PROTOCOL,
    DOMAIN,
    PROTOCOL_MATTER,
    PROTOCOL_ZIGBEE,
)
from .options_flow import SmartSwitchOptionsFlow

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PROTOCOL): selector.SelectSelector(
            {"options": [PROTOCOL_MATTER, PROTOCOL_ZIGBEE]}
        ),
    }
)

STEP_MATTER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BUTTON1_ENTITY): selector.EntitySelector({"domain": "event"}),
        vol.Required(CONF_BUTTON2_ENTITY): selector.EntitySelector({"domain": "event"}),
    }
)

STEP_ZIGBEE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACTION_ENTITY): selector.EntitySelector({"domain": "event"}),
    }
)


class SmartSwitchControlConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._protocol: str | None = None

    async def async_step_user(self, user_input: dict | None = None):
        if user_input is not None:
            self._protocol = user_input[CONF_PROTOCOL]
            if self._protocol == PROTOCOL_MATTER:
                return await self.async_step_matter()
            return await self.async_step_zigbee()

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

    async def async_step_matter(self, user_input: dict | None = None):
        if user_input is not None:
            return self.async_create_entry(
                title="Smart Switch (Matter)",
                data={CONF_PROTOCOL: PROTOCOL_MATTER, **user_input},
            )
        return self.async_show_form(step_id="matter", data_schema=STEP_MATTER_SCHEMA)

    async def async_step_zigbee(self, user_input: dict | None = None):
        if user_input is not None:
            return self.async_create_entry(
                title="Smart Switch (Zigbee)",
                data={CONF_PROTOCOL: PROTOCOL_ZIGBEE, **user_input},
            )
        return self.async_show_form(step_id="zigbee", data_schema=STEP_ZIGBEE_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> SmartSwitchOptionsFlow:
        return SmartSwitchOptionsFlow(config_entry)
