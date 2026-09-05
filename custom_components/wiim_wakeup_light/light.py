"""Light platform for WiiM Wake-up Light devices."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, ClassVar

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    CommandKind,
    WiimWakeUpLightError,
    brightness_ha_to_percent,
    brightness_percent_to_ha,
    build_turn_on_commands,
)
from .const import DOMAIN, EFFECTS, OFFICIAL_WIIM_DOMAIN
from .coordinator import WiimWakeUpLightCoordinator
from .models import WiimWakeUpLightRuntimeData, WiimWakeUpLightState

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry[WiimWakeUpLightRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the light entity from a config entry."""
    async_add_entities([WiimWakeUpLightEntity(entry.runtime_data)])


class WiimWakeUpLightEntity(CoordinatorEntity[WiimWakeUpLightCoordinator], LightEntity):
    """Native Home Assistant light entity for the lamp."""

    _attr_color_mode = ColorMode.RGB
    _attr_effect_list: ClassVar[list[str]] = list(EFFECTS)
    _attr_has_entity_name = True
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_translation_key = "light"

    def __init__(self, runtime_data: WiimWakeUpLightRuntimeData) -> None:
        """Initialize the light entity."""
        super().__init__(runtime_data.coordinator)
        description = runtime_data.description
        self._description = description
        self._attr_unique_id = f"{description.udn}_light"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={
                (DOMAIN, description.udn),
                (OFFICIAL_WIIM_DOMAIN, description.udn),
            },
            name=description.name,
            manufacturer=description.manufacturer,
            model=description.model,
            sw_version=description.firmware,
            configuration_url=f"https://{description.host}",
        )

    @property
    def is_on(self) -> bool:
        """Return whether the lamp is on."""
        return self.coordinator.data.is_on

    @property
    def brightness(self) -> int | None:
        """Return brightness on Home Assistant's 0-255 scale."""
        percent = self.coordinator.data.brightness_percent
        return brightness_percent_to_ha(percent) if percent is not None else None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the current RGB color."""
        return self.coordinator.data.rgb_color

    @property
    def effect(self) -> str | None:
        """Return the current built-in effect."""
        return self.coordinator.data.effect

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Expose the three read-only diagnostic flags from getLightInfo."""
        data = self.coordinator.data
        return {
            key: value
            for key, value in (
                ("sleep_mode", data.sleep_mode),
                ("reading_routine", data.reading_routine),
                ("clock_display", data.clock_display),
            )
            if value is not None
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on or adjust the lamp in firmware-safe command order."""
        effect: str | None = kwargs.get(ATTR_EFFECT)
        if effect is not None and effect not in EFFECTS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_effect",
                translation_placeholders={"effect": effect},
            )

        raw_rgb = kwargs.get(ATTR_RGB_COLOR)
        rgb_color = tuple(raw_rgb) if raw_rgb is not None else None
        brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)
        commands = build_turn_on_commands(
            effect=effect,
            rgb_color=rgb_color,
            brightness=brightness,
        )
        command_sequence = self.coordinator.push_sequence

        try:
            for command in commands:
                if command.kind is CommandKind.EFFECT:
                    await self.coordinator.api.async_set_effect(str(command.value))
                elif command.kind is CommandKind.RGB:
                    await self.coordinator.api.async_set_rgb(command.value)  # type: ignore[arg-type]
                elif command.kind is CommandKind.BRIGHTNESS:
                    await self.coordinator.api.async_set_brightness(int(command.value))
                else:
                    await self.coordinator.api.async_set_power(
                        is_on=bool(command.value)
                    )
        except (ValueError, WiimWakeUpLightError) as err:
            _LOGGER.debug("Turn-on command failed: %s", err)
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": "turn on"},
            ) from err

        optimistic = _optimistic_turn_on_state(
            self.coordinator.data,
            effect=effect,
            rgb_color=rgb_color,
            brightness=brightness,
        )
        self.coordinator.async_set_optimistic_state(command_sequence, optimistic)
        self.coordinator.async_schedule_command_refresh(command_sequence)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn off without changing timers, modes, or routines."""
        command_sequence = self.coordinator.push_sequence
        try:
            await self.coordinator.api.async_set_power(is_on=False)
        except WiimWakeUpLightError as err:
            _LOGGER.debug("Turn-off command failed: %s", err)
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": "turn off"},
            ) from err

        self.coordinator.async_set_optimistic_state(
            command_sequence, self.coordinator.optimistic_power_off()
        )
        self.coordinator.async_schedule_command_refresh(command_sequence)


def _optimistic_turn_on_state(
    current: WiimWakeUpLightState,
    *,
    effect: str | None,
    rgb_color: tuple[int, int, int] | None,
    brightness: int | None,
) -> WiimWakeUpLightState:
    """Model documented command side effects until push or polling reconciles."""
    final_effect = effect if effect is not None else current.effect
    final_rgb = rgb_color if rgb_color is not None else current.rgb_color
    final_brightness = current.brightness_percent
    if rgb_color is not None:
        final_effect = "My favorite"
        final_brightness = 100
    if brightness is not None:
        final_brightness = brightness_ha_to_percent(brightness)
    return replace(
        current,
        is_on=True,
        brightness_percent=final_brightness,
        rgb_color=final_rgb,
        effect=final_effect,
    )
