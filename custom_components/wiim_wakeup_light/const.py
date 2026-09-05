"""Constants for the WiiM Wake-up Light integration."""

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "wiim_wakeup_light"
OFFICIAL_WIIM_DOMAIN: Final = "wiim"

PLATFORMS: Final = [Platform.LIGHT]

UPNP_PORT: Final = 49152
DESCRIPTION_PATH: Final = "/description.xml"
LIGHT_SERVICE_ID: Final = "urn:wiimu-com:serviceId:LightManager"
LIGHT_SERVICE_TYPE: Final = "urn:schemas-wiimu-com:service:LightManager:1"
LIGHT_EVENT_PATH: Final = "/upnp/event/LightManager1"

HTTP_TIMEOUT: Final = 8
SUBSCRIPTION_TIMEOUT: Final = timedelta(seconds=300)
SUBSCRIPTION_RETRY_SECONDS: Final = 30.0
SUBSCRIPTION_RENEWAL_MARGIN_SECONDS: Final = 60.0
FALLBACK_POLL_INTERVAL: Final = timedelta(seconds=60)
COMMAND_REFRESH_DELAY_SECONDS: Final = 1.5

PROJECT_IDENTIFIER: Final = "WiiM_Light"

EFFECTS: Final[dict[str, int]] = {
    "Reading": 1,
    "Night": 2,
    "Unwind": 3,
    "Energize": 4,
    "Relax": 5,
    "Restore": 6,
    "Rainbow": 7,
    "Fireworks": 8,
    "Deep Ocean": 9,
    "Forest": 10,
    "Music": 11,
    "Meditation": 12,
    "My favorite": 13,
    "Sunrise": 14,
    "Sunset": 15,
}

DEFAULT_MODE_SPEED: Final = 10
DEFAULT_MODE_DURATION: Final = 1
DEFAULT_MODE_STYLE: Final = 1
