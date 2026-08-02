from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids

from .const import (
    ACTION_DOUBLE_TAP,
    ACTION_HOLD_END,
    ACTION_HOLD_START,
    ACTION_TAP,
    CONF_ACTION_ENTITY,
    CONF_BUTTON1_ENTITY,
    CONF_BUTTON2_ENTITY,
    CONF_DOUBLE_TAP_COLOR,
    CONF_HOLD_RAMP,
    CONF_PROTOCOL,
    CONF_TAP_TOGGLE,
    CONF_TARGET,
    MATTER_EVENT_MAP,
    PROTOCOL_MATTER,
    RAMP_PERIOD_S,
    RAMP_UPDATE_INTERVAL_S,
    TEMP_STEPS_KELVIN,
    ZIGBEE_CHANNEL_PREFIXES,
    ZIGBEE_EVENT_SUFFIX_MAP,
)
from .ramp import Ramp

_LOGGER = logging.getLogger(__name__)


class _ButtonState:
    def __init__(self) -> None:
        self.direction = 1
        self.temp_index = 1
        self.ramp: Ramp | None = None
        self.cancel_ramp_timer = None


class SwitchController:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsubscribers: list = []
        self._button_state = {1: _ButtonState(), 2: _ButtonState()}

    @callback
    def async_setup(self) -> None:
        data = self.entry.data

        if data[CONF_PROTOCOL] == PROTOCOL_MATTER:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self.hass, [data[CONF_BUTTON1_ENTITY]], self._make_matter_listener(1)
                )
            )
            self._unsubscribers.append(
                async_track_state_change_event(
                    self.hass, [data[CONF_BUTTON2_ENTITY]], self._make_matter_listener(2)
                )
            )
        else:
            self._unsubscribers.append(
                async_track_state_change_event(self.hass, [data[CONF_ACTION_ENTITY]], self._zigbee_listener)
            )

    @callback
    def async_unload(self) -> None:
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()
        for state in self._button_state.values():
            if state.cancel_ramp_timer is not None:
                state.cancel_ramp_timer()
                state.cancel_ramp_timer = None

    def _button_options(self, button: int) -> dict:
        return self.entry.options.get(f"button{button}", {})

    @staticmethod
    def _event_type_from(event: Event) -> str | None:
        new_state = event.data["new_state"]
        if new_state is None:
            return None
        return new_state.attributes.get("event_type")

    def _make_matter_listener(self, button: int):
        @callback
        def _listener(event: Event) -> None:
            event_type = self._event_type_from(event)
            if event_type is None:
                return
            action = MATTER_EVENT_MAP.get(event_type)
            if action is not None:
                self._handle_action(button, action)

        return _listener

    @callback
    def _zigbee_listener(self, event: Event) -> None:
        event_type = self._event_type_from(event)
        if event_type is None:
            return

        for button, prefix in ZIGBEE_CHANNEL_PREFIXES.items():
            if event_type.startswith(f"{prefix}_"):
                suffix = event_type[len(prefix) + 1 :]
                action = ZIGBEE_EVENT_SUFFIX_MAP.get(suffix)
                if action is not None:
                    self._handle_action(button, action)
                return

    def _handle_action(self, button: int, action: str) -> None:
        options = self._button_options(button)
        if action == ACTION_TAP and options.get(CONF_TAP_TOGGLE, True):
            self._toggle(button)
        elif action == ACTION_HOLD_START and options.get(CONF_HOLD_RAMP, True):
            self._start_ramp(button)
        elif action == ACTION_HOLD_END and options.get(CONF_HOLD_RAMP, True):
            self._stop_ramp(button)
        elif action == ACTION_DOUBLE_TAP and options.get(CONF_DOUBLE_TAP_COLOR, True):
            self._cycle_temp(button)

    def _resolve_lights(self, button: int) -> set[str]:
        target = self._button_options(button).get(CONF_TARGET, {})
        if not target:
            return set()
        selection = TargetSelection(target)
        selected = async_extract_referenced_entity_ids(self.hass, selection)
        entities = selected.referenced | selected.indirectly_referenced
        return {e for e in entities if e.startswith("light.")}

    def _current_brightness_pct(self, lights: set[str]) -> int:
        if not lights:
            return 0
        state = self.hass.states.get(next(iter(lights)))
        if state is None:
            return 0
        brightness = state.attributes.get("brightness")
        if brightness is None:
            return 0
        return round(int(brightness) / 255 * 100)

    def _toggle(self, button: int) -> None:
        lights = self._resolve_lights(button)
        if not lights:
            return
        self.hass.async_create_task(
            self.hass.services.async_call("light", "toggle", {"entity_id": list(lights)})
        )

    def _start_ramp(self, button: int) -> None:
        state = self._button_state[button]
        start_pct = self._current_brightness_pct(self._resolve_lights(button))
        # Flip direction so each new hold continues the opposite way.
        state.direction = -state.direction
        state.ramp = Ramp(start_pct=start_pct, direction=state.direction, period=RAMP_PERIOD_S)

        @callback
        def _tick(_now) -> None:
            if state.ramp is None:
                return
            pct, _ = state.ramp.state()
            lights = self._resolve_lights(button)
            if lights:
                self.hass.async_create_task(
                    self.hass.services.async_call(
                        "light", "turn_on", {"entity_id": list(lights), "brightness_pct": pct}
                    )
                )

        state.cancel_ramp_timer = async_track_time_interval(
            self.hass, _tick, timedelta(seconds=RAMP_UPDATE_INTERVAL_S)
        )

    def _stop_ramp(self, button: int) -> None:
        state = self._button_state[button]
        # Remember whichever direction the sweep was actually moving at release.
        if state.ramp is not None:
            _, state.direction = state.ramp.state()
        state.ramp = None
        if state.cancel_ramp_timer is not None:
            state.cancel_ramp_timer()
            state.cancel_ramp_timer = None

    def _cycle_temp(self, button: int) -> None:
        state = self._button_state[button]
        state.temp_index = (state.temp_index + 1) % len(TEMP_STEPS_KELVIN)
        kelvin = TEMP_STEPS_KELVIN[state.temp_index]
        lights = self._resolve_lights(button)
        if lights:
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "light", "turn_on", {"entity_id": list(lights), "color_temp_kelvin": kelvin}
                )
            )
