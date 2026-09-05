"""Data models for the WiiM Wake-up Light integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import WiimWakeUpLightCoordinator
    from .upnp import WiimWakeUpLightSubscription


@dataclass(frozen=True, slots=True)
class WiimWakeUpLightState:
    """Confirmed or temporarily optimistic lamp state."""

    is_on: bool
    brightness_percent: int | None = None
    rgb_color: tuple[int, int, int] | None = None
    effect: str | None = None
    sleep_mode: int | None = None
    reading_routine: int | None = None
    clock_display: int | None = None


@dataclass(frozen=True, slots=True)
class WiimWakeUpLightDescription:
    """Stable device metadata discovered from HTTP and UPnP."""

    host: str
    udn: str
    uuid: str
    name: str
    manufacturer: str
    model: str
    firmware: str | None
    light_api_version: str | None
    mac_address: str | None
    event_sub_url: str


@dataclass(slots=True)
class WiimWakeUpLightRuntimeData:
    """Runtime objects owned by one config entry."""

    coordinator: WiimWakeUpLightCoordinator
    description: WiimWakeUpLightDescription
    subscription: WiimWakeUpLightSubscription
