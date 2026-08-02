from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .controller import SwitchController


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    controller = SwitchController(hass, entry)
    controller.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    controller: SwitchController = hass.data[DOMAIN].pop(entry.entry_id)
    controller.async_unload()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
