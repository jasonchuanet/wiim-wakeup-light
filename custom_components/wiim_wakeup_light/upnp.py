"""UPnP push subscriptions for WiiM Wake-up Light devices."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass

import aiohttp
from async_upnp_client.aiohttp import AiohttpNotifyServer, AiohttpSessionRequester
from async_upnp_client.client import UpnpService, UpnpStateVariable
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.event_handler import UpnpEventHandler
from async_upnp_client.exceptions import UpnpError
from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .api import parse_last_change
from .const import (
    HTTP_TIMEOUT,
    LIGHT_SERVICE_ID,
    LIGHT_SERVICE_TYPE,
    SUBSCRIPTION_RENEWAL_MARGIN_SECONDS,
    SUBSCRIPTION_RETRY_SECONDS,
    SUBSCRIPTION_TIMEOUT,
)
from .models import WiimWakeUpLightState

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _EventServerLease:
    """One shared callback server bound to a local interface."""

    source_ip: str
    server: AiohttpNotifyServer
    references: int = 1


class EventServerManager:
    """Share one async-upnp-client callback server per local interface."""

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession) -> None:
        """Initialize the server manager."""
        self._hass = hass
        self.requester = AiohttpSessionRequester(
            session, with_sleep=True, timeout=HTTP_TIMEOUT
        )
        self._servers: dict[str, _EventServerLease] = {}
        self._lock = asyncio.Lock()

    async def async_acquire(self, device_host: str) -> _EventServerLease:
        """Acquire a callback server reachable from a specific device."""
        source_ip = await async_get_source_ip(self._hass, target_ip=device_host)
        async with self._lock:
            if lease := self._servers.get(source_ip):
                lease.references += 1
                return lease

            server = AiohttpNotifyServer(
                self.requester, (source_ip, 0), loop=self._hass.loop
            )
            await server.async_start_server()
            lease = _EventServerLease(source_ip=source_ip, server=server)
            self._servers[source_ip] = lease
            return lease

    async def async_release(self, lease: _EventServerLease) -> None:
        """Release and, when unused, stop a callback server."""
        server_to_stop: AiohttpNotifyServer | None = None
        async with self._lock:
            current = self._servers.get(lease.source_ip)
            if current is not lease:
                return
            current.references -= 1
            if current.references == 0:
                self._servers.pop(lease.source_ip)
                server_to_stop = current.server
        if server_to_stop is not None:
            await server_to_stop.async_stop_server()

    async def async_close(self) -> None:
        """Stop every remaining callback server."""
        async with self._lock:
            servers = [lease.server for lease in self._servers.values()]
            self._servers.clear()
        for server in servers:
            await server.async_stop_server()


class WiimWakeUpLightSubscription:
    """Manage subscribe, renew, retry, and unsubscribe for one lamp."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        manager: EventServerManager,
        host: str,
        description_url: str,
        on_state: Callable[[WiimWakeUpLightState], None],
    ) -> None:
        """Initialize a LightManager subscription."""
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._host = host
        self._description_url = description_url
        self._on_state = on_state
        self._lease: _EventServerLease | None = None
        self._service: UpnpService | None = None
        self._sid: str | None = None
        self._next_renewal_seconds = SUBSCRIPTION_RETRY_SECONDS
        self._renewal_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._stopped = False

    @property
    def active(self) -> bool:
        """Return whether a subscription SID is currently active."""
        return self._sid is not None

    async def async_start(self) -> bool:
        """Start eventing without making polling setup depend on it."""
        async with self._start_lock:
            if self._stopped:
                return False
            if self._renewal_task is not None and not self._renewal_task.done():
                return self.active
            try:
                if self._service is None:
                    await self._async_prepare_service()
                if self._lease is None:
                    self._lease = await self._manager.async_acquire(self._host)
                await self._async_subscribe()
            except (
                aiohttp.ClientError,
                HomeAssistantError,
                OSError,
                UpnpError,
                ValueError,
            ) as err:
                self._sid = None
                _LOGGER.warning(
                    "Unable to enable push updates for WiiM Wake-up Light at %s; "
                    "falling back to polling: %s",
                    self._host,
                    err,
                )

            if self._lease is not None:
                self._renewal_task = self._entry.async_create_background_task(
                    self._hass,
                    self._async_renewal_loop(),
                    name=f"{self._entry.title} LightManager subscription",
                )
            return self.active

    async def async_ensure_started(self) -> None:
        """Retry callback setup after a prior bind or routing failure."""
        if self._stopped:
            return
        if self._renewal_task is None or self._renewal_task.done():
            await self.async_start()

    async def _async_prepare_service(self) -> None:
        """Load the advertised proprietary service from description.xml."""
        device = await UpnpFactory(
            self._manager.requester, non_strict=True
        ).async_create_device(self._description_url)
        service = next(
            (
                candidate
                for candidate in device.all_services
                if candidate.service_id == LIGHT_SERVICE_ID
                or candidate.service_type == LIGHT_SERVICE_TYPE
            ),
            None,
        )
        if service is None:
            raise UpnpError("Device description has no LightManager service")
        service.on_event = self._async_handle_event
        self._service = service

    async def _async_subscribe(self) -> None:
        """Request a new subscription and remember its renewal interval."""
        assert self._lease is not None
        assert self._service is not None
        sid, timeout = await self._lease.server.event_handler.async_subscribe(
            self._service, timeout=SUBSCRIPTION_TIMEOUT
        )
        self._sid = sid
        self._next_renewal_seconds = _renewal_delay(timeout.total_seconds())
        _LOGGER.debug("Subscribed to LightManager at %s with SID %s", self._host, sid)

    async def _async_renew_once(self) -> float:
        """Renew or recreate a subscription and return the next delay."""
        if self._lease is None or self._service is None:
            return SUBSCRIPTION_RETRY_SECONDS
        event_handler: UpnpEventHandler = self._lease.server.event_handler
        try:
            if self._sid is None:
                sid, timeout = await event_handler.async_subscribe(
                    self._service, timeout=SUBSCRIPTION_TIMEOUT
                )
            else:
                sid, timeout = await event_handler.async_resubscribe(
                    self._sid, timeout=SUBSCRIPTION_TIMEOUT
                )
        except (KeyError, UpnpError) as err:
            self._sid = None
            _LOGGER.debug(
                "LightManager subscription renewal failed for %s: %s",
                self._host,
                err,
            )
            return SUBSCRIPTION_RETRY_SECONDS

        self._sid = sid
        return _renewal_delay(timeout.total_seconds())

    async def _async_renewal_loop(self) -> None:
        """Renew before expiry and resubscribe after failures or device reboot."""
        delay = self._next_renewal_seconds
        while not self._stopped:
            await asyncio.sleep(delay)
            delay = await self._async_renew_once()

    @callback
    def _async_handle_event(
        self,
        _service: UpnpService,
        state_variables: Sequence[UpnpStateVariable],
    ) -> None:
        """Parse a LightManager event and publish only valid light state."""
        for state_variable in state_variables:
            if state_variable.name != "LastChange":
                continue
            if state := parse_last_change(str(state_variable.value or "")):
                self._on_state(state)
            else:
                _LOGGER.debug(
                    "Ignored empty, malformed, or unrelated LightManager event from %s",
                    self._host,
                )

    async def async_stop(self) -> None:
        """Cancel renewal, unsubscribe, and release the callback server."""
        self._stopped = True
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._renewal_task
            self._renewal_task = None

        if self._lease is not None and self._sid is not None:
            with suppress(KeyError, UpnpError):
                await self._lease.server.event_handler.async_unsubscribe(self._sid)
        self._sid = None

        if self._service is not None:
            self._service.on_event = None
        if self._lease is not None:
            await self._manager.async_release(self._lease)
            self._lease = None


def _renewal_delay(timeout_seconds: float) -> float:
    """Choose a renewal point comfortably before subscription expiry."""
    margin = min(
        SUBSCRIPTION_RENEWAL_MARGIN_SECONDS,
        max(5.0, timeout_seconds * 0.2),
    )
    return max(5.0, timeout_seconds - margin)
