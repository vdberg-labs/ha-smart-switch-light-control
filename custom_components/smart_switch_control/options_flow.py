from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import BUTTON_OPTION_KEYS, CONF_DOUBLE_TAP_COLOR, CONF_HOLD_RAMP, CONF_TAP_TOGGLE, CONF_TARGET

LIGHT_TARGET_SELECTOR = selector.TargetSelector({"entity": [{"domain": "light"}]})


def _schema_for_button(prefix: str, defaults: dict) -> dict:
    return {
        vol.Optional(f"{prefix}_{CONF_TARGET}", default=defaults.get(CONF_TARGET, {})): LIGHT_TARGET_SELECTOR,
        vol.Optional(
            f"{prefix}_{CONF_TAP_TOGGLE}", default=defaults.get(CONF_TAP_TOGGLE, True)
        ): selector.BooleanSelector(),
        vol.Optional(
            f"{prefix}_{CONF_HOLD_RAMP}", default=defaults.get(CONF_HOLD_RAMP, True)
        ): selector.BooleanSelector(),
        vol.Optional(
            f"{prefix}_{CONF_DOUBLE_TAP_COLOR}", default=defaults.get(CONF_DOUBLE_TAP_COLOR, True)
        ): selector.BooleanSelector(),
    }


class SmartSwitchOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            button1 = {key: user_input[f"button1_{key}"] for key in BUTTON_OPTION_KEYS}
            button2 = {key: user_input[f"button2_{key}"] for key in BUTTON_OPTION_KEYS}
            return self.async_create_entry(title="", data={"button1": button1, "button2": button2})

        options = self.config_entry.options
        schema = vol.Schema(
            {
                **_schema_for_button("button1", options.get("button1", {})),
                **_schema_for_button("button2", options.get("button2", {})),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
