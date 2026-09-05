"""The WiiM Wake-up Light integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    WiimWakeUpLightApi,
    WiimWakeUpLightConnectionError,
    WiimWakeUpLightInvalidResponse,
    WiimWakeUpLightUnsupportedDevice,
)
from .const import DOMAIN, PLATFORMS
from .coordinator import WiimWakeUpLightCoordinator
from .models import WiimWakeUpLightRuntimeData
from .upnp import EventServerManager, WiimWakeUpLightSubscription

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _DomainData:
    """Domain-level resources shared by all configured lamps."""

    event_manager: EventServerManager
    entries: set[str] = field(default_factory=set)


type WiimWakeUpLightConfigEntry = ConfigEntry[WiimWakeUpLightRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: WiimWakeUpLightConfigEntry
) -> bool:
    """Set up a WiiM Wake-up Light from a config entry."""
    session = async_get_clientsession(hass, verify_ssl=False)
    domain_data: _DomainData | None = hass.data.get(DOMAIN)
    if domain_data is None:
        domain_data = _DomainData(EventServerManager(hass, session))
        hass.data[DOMAIN] = domain_data

    host = entry.data[CONF_HOST]
    api = WiimWakeUpLightApi(session, host)
    try:
        description = await api.async_probe()
    except WiimWakeUpLightConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err
    except WiimWakeUpLightUnsupportedDevice as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="not_supported",
        ) from err
    except WiimWakeUpLightInvalidResponse as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = WiimWakeUpLightCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    subscription = WiimWakeUpLightSubscription(
        hass,
        entry,
        domain_data.event_manager,
        host,
        api.description_url,
        coordinator.async_handle_push,
    )
    coordinator.subscription = subscription
    await subscription.async_start()

    entry.runtime_data = WiimWakeUpLightRuntimeData(
        coordinator=coordinator,
        description=description,
        subscription=subscription,
    )
    domain_data.entries.add(entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: WiimWakeUpLightConfigEntry
) -> bool:
    """Unload a WiiM Wake-up Light config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    await entry.runtime_data.subscription.async_stop()
    domain_data: _DomainData = hass.data[DOMAIN]
    domain_data.entries.discard(entry.entry_id)
    if not domain_data.entries:
        await domain_data.event_manager.async_close()
        hass.data.pop(DOMAIN, None)
    return True
