"""Diagnostics for the WiiM Wake-up Light integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import WiimWakeUpLightConfigEntry

TO_REDACT = {CONF_HOST, "uuid", "udn", "mac_address"}


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: WiimWakeUpLightConfigEntry
) -> dict[str, Any]:
    """Return non-mutating diagnostics with network identifiers redacted."""
    runtime = entry.runtime_data
    state = runtime.coordinator.data
    return async_redact_data(
        {
            "entry": entry.as_dict(),
            "device": {
                "host": runtime.description.host,
                "udn": runtime.description.udn,
                "uuid": runtime.description.uuid,
                "name": runtime.description.name,
                "manufacturer": runtime.description.manufacturer,
                "model": runtime.description.model,
                "firmware": runtime.description.firmware,
                "light_api_version": runtime.description.light_api_version,
                "mac_address": runtime.description.mac_address,
            },
            "push_active": runtime.subscription.active,
            "coordinator_last_update_success": runtime.coordinator.last_update_success,
            "state": {
                "is_on": state.is_on,
                "brightness_percent": state.brightness_percent,
                "rgb_color": state.rgb_color,
                "effect": state.effect,
                "sleep_mode": state.sleep_mode,
                "reading_routine": state.reading_routine,
                "clock_display": state.clock_display,
            },
        },
        TO_REDACT,
    )
