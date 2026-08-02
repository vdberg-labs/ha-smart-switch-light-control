DOMAIN = "smart_switch_control"

CONF_PROTOCOL = "protocol"
PROTOCOL_MATTER = "matter"
PROTOCOL_ZIGBEE = "zigbee"

CONF_BUTTON1_ENTITY = "button1_entity"
CONF_BUTTON2_ENTITY = "button2_entity"
CONF_ACTION_ENTITY = "action_entity"

CONF_TARGET = "target"
CONF_TAP_TOGGLE = "tap_toggle"
CONF_HOLD_RAMP = "hold_ramp"
CONF_DOUBLE_TAP_COLOR = "double_tap_color"

BUTTON_OPTION_KEYS = (CONF_TARGET, CONF_TAP_TOGGLE, CONF_HOLD_RAMP, CONF_DOUBLE_TAP_COLOR)

# Zigbee external converter (data/external_converters/smart_switch.mjs) always
# uses these channel prefixes on the combined action entity.
ZIGBEE_CHANNEL_PREFIXES = {1: "switch1", 2: "switch2"}

ACTION_TAP = "tap"
ACTION_DOUBLE_TAP = "double_tap"
ACTION_HOLD_START = "hold_start"
ACTION_HOLD_END = "hold_end"

# HA's matter integration reports MultiPressComplete as multi_press_<count>.
MATTER_EVENT_MAP = {
    "multi_press_1": ACTION_TAP,
    "multi_press_2": ACTION_DOUBLE_TAP,
    "long_press": ACTION_HOLD_START,
    "long_release": ACTION_HOLD_END,
}

ZIGBEE_EVENT_SUFFIX_MAP = {
    "tap_1": ACTION_TAP,
    "tap_2": ACTION_DOUBLE_TAP,
    "long_press": ACTION_HOLD_START,
    "long_release": ACTION_HOLD_END,
}

TEMP_STEPS_KELVIN = [2700, 3000, 3500, 4000, 6500]
RAMP_PERIOD_S = 4.0
RAMP_UPDATE_INTERVAL_S = 0.1
