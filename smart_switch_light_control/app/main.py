from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
from aiohttp import web

from ramp import Ramp

_LOGGER = logging.getLogger("smart_switch_light_control")

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/data/switches.json"))
PAGE_PATH = Path(__file__).parent / "index.html"
WS_URL = os.environ.get("HA_WS_URL", "http://supervisor/core/websocket")
INGRESS_PEER = os.environ.get("INGRESS_PEER", "172.30.32.2")
INGRESS_PORT = 8099
RECONNECT_DELAY_S = 5

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

# Zigbee2MQTT's external converter uses these prefixes on the combined action entity.
ZIGBEE_CHANNEL_PREFIXES = {1: "switch1", 2: "switch2"}
ZIGBEE_EVENT_SUFFIX_MAP = {
    "tap_1": ACTION_TAP,
    "tap_2": ACTION_DOUBLE_TAP,
    "long_press": ACTION_HOLD_START,
    "long_release": ACTION_HOLD_END,
}

TEMP_STEPS_KELVIN = [2700, 3000, 3500, 4000, 6500]
# Per-button ramp settings (the web UI's Advanced section): key -> (default, minimum, maximum)
RAMP_SETTINGS = {
    "sweep_s": (2.0, 0.5, 30.0),  # seconds from lowest to full brightness
    "step_ms": (300, 50, 5000),  # time between brightness commands
    "transition_ms": (300, 0, 5000),  # fade the light does for each command
    "min_pct": (1, 1, 50),  # lowest brightness the ramp goes to
}

TARGET_KEYS = ("entity_id", "area_id", "label_id")

# Zigbee2MQTT devices appear in Home Assistant through the MQTT integration.
PLATFORM_PROTOCOLS = {"matter": "matter", "mqtt": "zigbee"}


@dataclass
class Button:
    target: dict[str, list[str]]
    tap_toggle: bool
    hold_ramp: bool
    double_tap_color: bool
    ramp_settings: dict[str, float] = field(default_factory=lambda: {k: v[0] for k, v in RAMP_SETTINGS.items()})
    direction: int = 1
    temp_index: int = 1
    holding: bool = False
    ramp: Ramp | None = None
    ramp_task: asyncio.Task | None = None


@dataclass
class Switch:
    name: str
    buttons: dict[int, Button] = field(default_factory=dict)
    # (button number or None for a combined action entity, entity_id)
    subscriptions: list[tuple[int | None, str]] = field(default_factory=list)


def natural_key(text: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def find_devices(entities: list[dict], device_entries: list[dict]) -> dict[str, dict]:
    """Group event entities by device, so a switch can be picked as one thing."""
    grouped: dict[str, list[dict]] = {}
    for entity in entities:
        if not entity["entity_id"].startswith("event.") or entity.get("disabled_by") or not entity.get("device_id"):
            continue
        if entity.get("platform") == "mqtt" and "action" not in entity["entity_id"]:
            continue
        if entity.get("platform") in PLATFORM_PROTOCOLS:
            grouped.setdefault(entity["device_id"], []).append(entity)

    devices: dict[str, dict] = {}
    for entry in device_entries:
        found = grouped.get(entry["id"])
        if not found:
            continue
        found.sort(key=lambda e: natural_key(e.get("unique_id") or e["entity_id"]))
        protocol = PLATFORM_PROTOCOLS[found[0]["platform"]]
        devices[entry["id"]] = {
            "id": entry["id"],
            "name": entry.get("name_by_user") or entry.get("name") or entry["id"],
            "model": entry.get("model") or "",
            "protocol": protocol,
            "entities": [e["entity_id"] for e in found],
            "buttons": 2 if protocol == "zigbee" else min(len(found), 2),
        }
    return devices


def normalize(config: list[dict], devices: dict[str, dict]) -> list[dict]:
    """Give entries saved before devices were tracked their device_id."""
    owners = {entity: device_id for device_id, device in devices.items() for entity in device["entities"]}
    normalized = []
    for raw in config:
        device_id = raw.get("device_id") or owners.get(raw.get("button1_entity") or raw.get("action_entity"))
        if device_id is None:
            _LOGGER.error("Dropping a saved switch whose device no longer exists: %s", raw)
            continue
        legacy = {"button1_enabled": True, "button2_enabled": True} if "device_id" not in raw else {}
        normalized.append({**legacy, **raw, "device_id": device_id})
    return normalized


def load_switches(config: list[dict], devices: dict[str, dict]) -> list[Switch]:
    switches: list[Switch] = []
    for raw in config:
        device = devices.get(raw["device_id"])
        if device is None:
            _LOGGER.error("Switch device %s was not found - skipping it", raw["device_id"])
            continue
        name = raw.get("name") or device["name"]
        enabled = [n for n in range(1, device["buttons"] + 1) if raw.get(f"button{n}_enabled", False)]
        if not enabled:
            _LOGGER.warning("%s has no buttons switched on - ignoring it", name)
            continue
        if device["protocol"] == "matter":
            subscriptions = [(number, device["entities"][number - 1]) for number in enabled]
        else:
            subscriptions = [(None, device["entities"][0])]
        buttons = {
            number: Button(
                target={k: v for k, v in raw.get(f"button{number}_targets", {}).items() if k in TARGET_KEYS and v},
                tap_toggle=raw.get(f"button{number}_tap_toggle", True),
                hold_ramp=raw.get(f"button{number}_hold_ramp", True),
                double_tap_color=raw.get(f"button{number}_double_tap_color", True),
                ramp_settings={key: raw.get(f"button{number}_{key}", default) for key, (default, _, _) in RAMP_SETTINGS.items()},
            )
            for number in enabled
        }
        for number, button in buttons.items():
            if not button.target:
                _LOGGER.warning("%s button %d has no lights chosen", name, number)
        switches.append(Switch(name, buttons, subscriptions))
    return switches


class Controller:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse, config: list[dict]) -> None:
        self._ws = ws
        self._config = config
        self._switches: list[Switch] = []
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[int, tuple[Switch, int | None]] = {}
        self._tasks: set[asyncio.Task] = set()

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _request(self, message: dict):
        msg_id = self._new_id()
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await self._ws.send_json({"id": msg_id, **message})
        return await future

    async def _call_light(self, service: str, lights: list[str], **data) -> None:
        await self._ws.send_json(
            {
                "id": self._new_id(),
                "type": "call_service",
                "domain": "light",
                "service": service,
                "service_data": data,
                "target": {"entity_id": lights},
            }
        )

    async def _resolve_lights(self, button: Button) -> list[str]:
        if not button.target:
            return []
        result = await self._request({"type": "extract_from_target", "target": button.target})
        return sorted(e for e in result["referenced_entities"] if e.startswith("light."))

    async def devices(self) -> dict[str, dict]:
        entities, device_entries = await asyncio.gather(
            self._request({"type": "config/entity_registry/list"}),
            self._request({"type": "config/device_registry/list"}),
        )
        return find_devices(entities, device_entries)

    async def registry(self) -> dict:
        devices, states, areas, labels = await asyncio.gather(
            self.devices(),
            self._request({"type": "get_states"}),
            self._request({"type": "config/area_registry/list"}),
            self._request({"type": "config/label_registry/list"}),
        )

        def named(domain: str) -> list[dict]:
            found = (s for s in states if s["entity_id"].startswith(f"{domain}."))
            items = [{"id": s["entity_id"], "name": s["attributes"].get("friendly_name", s["entity_id"])} for s in found]
            return sorted(items, key=lambda item: item["name"].lower())

        def listed(entries: list[dict], key: str) -> list[dict]:
            return sorted(({"id": e[key], "name": e["name"]} for e in entries), key=lambda item: item["name"].lower())

        return {
            "devices": sorted(devices.values(), key=lambda device: device["name"].lower()),
            "lights": named("light"),
            "areas": listed(areas, "area_id"),
            "labels": listed(labels, "label_id"),
            "ramp_settings": {key: {"default": d, "min": lo, "max": hi} for key, (d, lo, hi) in RAMP_SETTINGS.items()},
        }

    async def _read(self) -> None:
        try:
            async for message in self._ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    break
                self._dispatch(message.json())
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Connection to Home Assistant closed"))
            self._pending.clear()

    async def run(self) -> None:
        reader = asyncio.create_task(self._read())
        try:
            devices = await self.devices()
            self._switches = load_switches(normalize(self._config, devices), devices)
            for switch in self._switches:
                for number, entity in switch.subscriptions:
                    msg_id = self._new_id()
                    self._subscriptions[msg_id] = (switch, number)
                    await self._ws.send_json(
                        {
                            "id": msg_id,
                            "type": "subscribe_trigger",
                            "trigger": {"platform": "state", "entity_id": entity},
                        }
                    )
            for switch in self._switches:
                _LOGGER.info("Listening to %s: %s", switch.name, ", ".join(e for _, e in switch.subscriptions))
            _LOGGER.info("Listening to %d switch(es)", len(self._switches))
            await reader
        finally:
            reader.cancel()
            for task in list(self._tasks):
                task.cancel()
            for switch in self._switches:
                for button in switch.buttons.values():
                    button.holding = False
                    button.ramp = None
                    if button.ramp_task is not None:
                        button.ramp_task.cancel()
                        button.ramp_task = None

    def _dispatch(self, message: dict) -> None:
        msg_id = message.get("id")
        if message.get("type") == "result":
            future = self._pending.pop(msg_id, None)
            if message.get("success"):
                if future is not None and not future.done():
                    future.set_result(message.get("result"))
                return
            error = message.get("error", {})
            _LOGGER.error("Home Assistant rejected request %s: %s", msg_id, error.get("message", error))
            if future is not None and not future.done():
                future.set_exception(RuntimeError(error.get("message", "request failed")))
        elif message.get("type") == "event" and msg_id in self._subscriptions:
            switch, button = self._subscriptions[msg_id]
            to_state = message["event"]["variables"]["trigger"].get("to_state") or {}
            event_type = to_state.get("attributes", {}).get("event_type")
            if event_type:
                self._on_event(switch, button, event_type)

    def _on_event(self, switch: Switch, number: int | None, event_type: str) -> None:
        if number is not None:
            action = MATTER_EVENT_MAP.get(event_type)
            if action is not None:
                _LOGGER.info("%s button %d: %s", switch.name, number, action)
                self._handle(switch.buttons[number], action)
            return
        for button_number, prefix in ZIGBEE_CHANNEL_PREFIXES.items():
            if event_type.startswith(f"{prefix}_"):
                action = ZIGBEE_EVENT_SUFFIX_MAP.get(event_type[len(prefix) + 1 :])
                button = switch.buttons.get(button_number)
                if action is not None and button is not None:
                    _LOGGER.info("%s button %d: %s", switch.name, button_number, action)
                    self._handle(button, action)
                return

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(self._guarded(coroutine))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _guarded(coroutine) -> None:
        try:
            await coroutine
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Handling a switch action failed")

    def _handle(self, button: Button, action: str) -> None:
        if action == ACTION_TAP and button.tap_toggle:
            self._spawn(self._toggle(button))
        elif action == ACTION_HOLD_START and button.hold_ramp:
            button.holding = True
            self._spawn(self._start_ramp(button))
        elif action == ACTION_HOLD_END and button.hold_ramp:
            self._stop_ramp(button)
        elif action == ACTION_DOUBLE_TAP and button.double_tap_color:
            self._spawn(self._cycle_temp(button))

    async def _toggle(self, button: Button) -> None:
        lights = await self._resolve_lights(button)
        if lights:
            await self._call_light("toggle", lights)

    async def _current_brightness_pct(self, lights: list[str]) -> int:
        if not lights:
            return 0
        states = await self._request({"type": "get_states"})
        state = next((s for s in states if s["entity_id"] == lights[0]), None)
        brightness = state["attributes"].get("brightness") if state else None
        return round(int(brightness) / 255 * 100) if brightness is not None else 0

    async def _start_ramp(self, button: Button) -> None:
        lights = await self._resolve_lights(button)
        start_pct = await self._current_brightness_pct(lights)
        if not button.holding or not lights:
            return
        settings = button.ramp_settings
        # Flip direction so each new hold continues the opposite way.
        button.direction = -button.direction
        ramp = Ramp(start_pct=start_pct, direction=button.direction, period=2 * settings["sweep_s"],
                    min_pct=settings["min_pct"])
        button.ramp = ramp
        button.ramp_task = asyncio.create_task(self._guarded(self._ramp_loop(button, ramp, lights)))

    async def _ramp_loop(self, button: Button, ramp: Ramp, lights: list[str]) -> None:
        # Each command waits for the previous one, so a slow light can't build up a queue of them.
        loop = asyncio.get_running_loop()
        settings = button.ramp_settings
        step_s = settings["step_ms"] / 1000
        last = None
        while button.ramp is ramp:
            started = loop.time()
            pct, _ = ramp.state()
            if pct != last:
                await self._request(
                    {
                        "type": "call_service",
                        "domain": "light",
                        "service": "turn_on",
                        "service_data": {"brightness_pct": pct, "transition": settings["transition_ms"] / 1000},
                        "target": {"entity_id": lights},
                    }
                )
                last = pct
            await asyncio.sleep(max(0.0, step_s - (loop.time() - started)))

    def _stop_ramp(self, button: Button) -> None:
        button.holding = False
        # Remember whichever direction the sweep was actually moving at release.
        if button.ramp is not None:
            _, button.direction = button.ramp.state()
        button.ramp = None
        if button.ramp_task is not None:
            button.ramp_task.cancel()
            button.ramp_task = None

    async def _cycle_temp(self, button: Button) -> None:
        button.temp_index = (button.temp_index + 1) % len(TEMP_STEPS_KELVIN)
        kelvin = TEMP_STEPS_KELVIN[button.temp_index]
        lights = await self._resolve_lights(button)
        if lights:
            await self._call_light("turn_on", lights, color_temp_kelvin=kelvin)


async def authenticate(ws: aiohttp.ClientWebSocketResponse, token: str) -> None:
    hello = await ws.receive_json()
    if hello.get("type") != "auth_required":
        raise RuntimeError(f"Unexpected greeting: {hello}")
    await ws.send_json({"type": "auth", "access_token": token})
    result = await ws.receive_json()
    if result.get("type") != "auth_ok":
        raise RuntimeError(f"Authentication failed: {result.get('message', result)}")


def valid_config(config: object) -> bool:
    if not isinstance(config, list):
        return False
    seen: set[str] = set()
    for raw in config:
        if not isinstance(raw, dict):
            return False
        device_id = raw.get("device_id")
        if not isinstance(device_id, str) or not device_id or device_id in seen:
            return False
        seen.add(device_id)
        if not isinstance(raw.get("name", ""), str):
            return False
        for number in (1, 2):
            target = raw.get(f"button{number}_targets", {})
            if not isinstance(target, dict):
                return False
            if not all(isinstance(v, list) and all(isinstance(i, str) for i in v) for v in target.values()):
                return False
            for key, (_, low, high) in RAMP_SETTINGS.items():
                value = raw.get(f"button{number}_{key}")
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high):
                    return False
    return True


class App:
    def __init__(self) -> None:
        self.config: list[dict] = json.loads(CONFIG_PATH.read_text("utf8")) if CONFIG_PATH.exists() else []
        self.controller: Controller | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.reloading = False

    @web.middleware
    async def _ingress_only(self, request: web.Request, handler):
        if request.remote != INGRESS_PEER:
            raise web.HTTPForbidden(text="Only available through Home Assistant")
        return await handler(request)

    async def _page(self, _request: web.Request) -> web.FileResponse:
        return web.FileResponse(PAGE_PATH)

    async def _get_registry(self, _request: web.Request) -> web.Response:
        if self.controller is None:
            return web.json_response({"error": "Not connected to Home Assistant"}, status=503)
        try:
            return web.json_response(await self.controller.registry())
        except RuntimeError as err:
            return web.json_response({"error": str(err)}, status=502)

    async def _get_switches(self, _request: web.Request) -> web.Response:
        if self.controller is None:
            return web.json_response({"error": "Not connected to Home Assistant"}, status=503)
        try:
            return web.json_response(normalize(self.config, await self.controller.devices()))
        except RuntimeError as err:
            return web.json_response({"error": str(err)}, status=502)

    async def _put_switches(self, request: web.Request) -> web.Response:
        try:
            config = await request.json()
        except ValueError:
            config = None
        if not valid_config(config):
            return web.json_response({"error": "Invalid configuration, or the same switch was added twice"}, status=400)

        temp = CONFIG_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(config), "utf8")
        temp.replace(CONFIG_PATH)
        self.config = config
        _LOGGER.info("Configuration saved")
        if self.ws is not None:
            self.reloading = True
            await self.ws.close()
        return web.json_response({"status": "ok"})

    async def start_web(self) -> None:
        app = web.Application(middlewares=[self._ingress_only])
        app.router.add_get("/", self._page)
        app.router.add_get("/api/registry", self._get_registry)
        app.router.add_get("/api/switches", self._get_switches)
        app.router.add_put("/api/switches", self._put_switches)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", INGRESS_PORT).start()

    async def run(self) -> None:
        token = os.environ["SUPERVISOR_TOKEN"]
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.ws_connect(WS_URL, max_msg_size=0, heartbeat=30) as ws:
                        await authenticate(ws, token)
                        _LOGGER.info("Connected to Home Assistant")
                        self.ws = ws
                        self.controller = Controller(ws, self.config)
                        await self.controller.run()
                    if not self.reloading:
                        _LOGGER.warning("Connection to Home Assistant closed")
                except (aiohttp.ClientError, TimeoutError, RuntimeError, ValueError) as err:
                    _LOGGER.warning("Home Assistant connection failed: %s", err)
                finally:
                    self.ws = None
                    self.controller = None
                if self.reloading:
                    self.reloading = False
                    continue
                await asyncio.sleep(RECONNECT_DELAY_S)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = App()
    await app.start_web()
    await app.run()


if __name__ == "__main__":
    asyncio.run(main())
