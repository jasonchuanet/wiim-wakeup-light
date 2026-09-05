"""State coordinator for the WiiM Wake-up Light integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import WiimWakeUpLightApi, WiimWakeUpLightError
from .const import COMMAND_REFRESH_DELAY_SECONDS, FALLBACK_POLL_INTERVAL
from .models import WiimWakeUpLightState

if TYPE_CHECKING:
    from .upnp import WiimWakeUpLightSubscription

_LOGGER = logging.getLogger(__name__)


class WiimWakeUpLightCoordinator(DataUpdateCoordinator[WiimWakeUpLightState]):
    """Coordinate push state and conservative recovery polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: WiimWakeUpLightApi,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} light",
            update_interval=FALLBACK_POLL_INTERVAL,
            always_update=False,
        )
        self.api = api
        self.entry = entry
        self.subscription: WiimWakeUpLightSubscription | None = None
        self.push_sequence = 0
        self._command_refresh_task: asyncio.Task[None] | None = None

    async def _async_update_data(self) -> WiimWakeUpLightState:
        """Poll complete state for availability and eventing recovery."""
        try:
            state = await self.api.async_get_light_info()
        except WiimWakeUpLightError as err:
            raise UpdateFailed(str(err)) from err

        if self.subscription is not None and not self.subscription.active:
            self.entry.async_create_background_task(
                self.hass,
                self.subscription.async_ensure_started(),
                name=f"{self.entry.title} LightManager recovery",
            )
        return state

    @callback
    def async_handle_push(self, state: WiimWakeUpLightState) -> None:
        """Accept a successfully parsed push update."""
        self.push_sequence += 1
        self.async_set_updated_data(state)

    @callback
    def async_set_optimistic_state(
        self, command_sequence: int, state: WiimWakeUpLightState
    ) -> None:
        """Publish a temporary UI state unless push already confirmed a command."""
        if self.push_sequence == command_sequence:
            self.async_set_updated_data(state)

    @callback
    def async_schedule_command_refresh(self, command_sequence: int) -> None:
        """Poll only if no push update arrives shortly after a command."""
        if self._command_refresh_task is not None:
            self._command_refresh_task.cancel()
        self._command_refresh_task = self.entry.async_create_background_task(
            self.hass,
            self._async_refresh_if_push_missing(command_sequence),
            name=f"{self.entry.title} post-command light refresh",
        )

    async def _async_refresh_if_push_missing(self, command_sequence: int) -> None:
        """Reconcile optimistic state when eventing is delayed or unavailable."""
        await asyncio.sleep(COMMAND_REFRESH_DELAY_SECONDS)
        if self.push_sequence == command_sequence:
            await self.async_request_refresh()

    @callback
    def optimistic_power_off(self) -> WiimWakeUpLightState:
        """Return current state with only power changed."""
        return replace(self.data, is_on=False)

    async def async_shutdown(self) -> None:
        """Cancel command reconciliation and coordinator polling."""
        if self._command_refresh_task is not None:
            self._command_refresh_task.cancel()
            self._command_refresh_task = None
        await super().async_shutdown()
