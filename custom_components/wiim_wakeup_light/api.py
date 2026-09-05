"""Local HTTP API client for WiiM Wake-up Light devices."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from html import unescape
from json import JSONDecodeError
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
from defusedxml import ElementTree as DET

from .const import (
    DEFAULT_MODE_DURATION,
    DEFAULT_MODE_SPEED,
    DEFAULT_MODE_STYLE,
    DESCRIPTION_PATH,
    EFFECTS,
    HTTP_TIMEOUT,
    LIGHT_EVENT_PATH,
    LIGHT_SERVICE_ID,
    LIGHT_SERVICE_TYPE,
    PROJECT_IDENTIFIER,
    UPNP_PORT,
)
from .models import WiimWakeUpLightDescription, WiimWakeUpLightState


class WiimWakeUpLightError(Exception):
    """Base exception for the WiiM Wake-up Light API."""


class WiimWakeUpLightConnectionError(WiimWakeUpLightError):
    """Raised when the lamp cannot be reached."""


class WiimWakeUpLightInvalidResponse(WiimWakeUpLightError):
    """Raised when the lamp returns an invalid response."""


class WiimWakeUpLightCommandError(WiimWakeUpLightError):
    """Raised when the lamp rejects a command."""


class WiimWakeUpLightUnsupportedDevice(WiimWakeUpLightError):
    """Raised when a LinkPlay device is not a Wake-up Light."""


class CommandKind(StrEnum):
    """Supported light command types."""

    EFFECT = "effect"
    RGB = "rgb"
    BRIGHTNESS = "brightness"
    POWER = "power"


@dataclass(frozen=True, slots=True)
class LightCommand:
    """One ordered command in a Home Assistant turn-on request."""

    kind: CommandKind
    value: object


def _coerce_int(value: Any) -> int | None:
    """Return an integer for tolerant numeric API values."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _clamp(value: int, minimum: int, maximum: int) -> int:
    """Clamp an integer to an inclusive range."""
    return max(minimum, min(maximum, value))


def brightness_percent_to_ha(percent: int) -> int:
    """Convert the lamp's percentage brightness to Home Assistant's 0-255 scale."""
    return round(_clamp(percent, 0, 100) * 255 / 100)


def brightness_ha_to_percent(brightness: int) -> int:
    """Convert Home Assistant brightness to the lamp's supported 1-100 range."""
    return _clamp(round(_clamp(brightness, 0, 255) * 100 / 255), 1, 100)


def format_rgb_hex(rgb_color: tuple[int, int, int]) -> str:
    """Format an RGB tuple as the six-character value expected by the lamp."""
    if len(rgb_color) != 3 or any(
        isinstance(component, bool)
        or not isinstance(component, int)
        or not 0 <= component <= 255
        for component in rgb_color
    ):
        raise ValueError("RGB components must be integers from 0 through 255")
    return "".join(f"{component:02x}" for component in rgb_color)


def build_turn_on_commands(
    *,
    effect: str | None = None,
    rgb_color: tuple[int, int, int] | None = None,
    brightness: int | None = None,
) -> list[LightCommand]:
    """Build commands in the order required to preserve final brightness."""
    commands: list[LightCommand] = []
    if effect is not None:
        commands.append(LightCommand(CommandKind.EFFECT, effect))
    if rgb_color is not None:
        commands.append(LightCommand(CommandKind.RGB, rgb_color))
    if brightness is not None:
        commands.append(
            LightCommand(CommandKind.BRIGHTNESS, brightness_ha_to_percent(brightness))
        )
    if not commands:
        commands.append(LightCommand(CommandKind.POWER, True))
    return commands


def parse_light_state(payload: Any) -> WiimWakeUpLightState:
    """Parse either getLightInfo or the state object carried in LastChange."""
    if not isinstance(payload, dict):
        raise WiimWakeUpLightInvalidResponse("Light state is not an object")

    params: Any = payload
    if "params" in payload:
        result_code = _coerce_int(payload.get("state"))
        if result_code != 0:
            raise WiimWakeUpLightInvalidResponse(
                f"getLightInfo returned API result {payload.get('state')!r}"
            )
        params = payload["params"]

    if not isinstance(params, dict):
        raise WiimWakeUpLightInvalidResponse("Light state params are not an object")

    power = _coerce_int(params.get("light_on"))
    if power not in (0, 1):
        raise WiimWakeUpLightInvalidResponse("Light state has no valid light_on value")

    brightness = _coerce_int(params.get("light_brig"))
    if brightness is not None:
        brightness = _clamp(brightness, 0, 100)

    rgb_color: tuple[int, int, int] | None = None
    raw_rgb = params.get("light_color_RGB")
    if isinstance(raw_rgb, dict):
        components = tuple(_coerce_int(raw_rgb.get(key)) for key in ("R", "G", "B"))
        if all(
            component is not None and 0 <= component <= 255 for component in components
        ):
            rgb_color = (components[0], components[1], components[2])  # type: ignore[arg-type]

    effect_value = params.get("light_mode")
    effect = effect_value if isinstance(effect_value, str) and effect_value else None

    return WiimWakeUpLightState(
        is_on=bool(power),
        brightness_percent=brightness,
        rgb_color=rgb_color,
        effect=effect,
        sleep_mode=_coerce_int(params.get("light_sleep_mode")),
        reading_routine=_coerce_int(params.get("light_reading_routine")),
        clock_display=_coerce_int(params.get("light_clock_display")),
    )


def parse_last_change(last_change: str) -> WiimWakeUpLightState | None:
    """Parse the nested and escaped LightManager LastChange value."""
    if not last_change or not last_change.strip():
        return None

    nested_xml = last_change.strip()
    try:
        root = DET.fromstring(nested_xml)
    except (DET.ParseError, ValueError):
        # Some callers hand us the still-escaped value from the outer UPnP XML,
        # while async-upnp-client has already decoded that outer layer. Parse the
        # decoded value first so &quot; remains safe inside double-quoted XML
        # attributes, then fall back to removing exactly one outer escape layer.
        decoded_xml = unescape(nested_xml)
        if decoded_xml == nested_xml:
            return None
        try:
            root = DET.fromstring(decoded_xml)
        except (DET.ParseError, ValueError):
            return None

    change_value: str | None = None
    info_value: str | None = None
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name == "change":
            change_value = element.attrib.get("val")
        elif local_name == "info":
            info_value = element.attrib.get("val")

    if change_value not in (None, "light") or not info_value:
        return None

    try:
        payload = json.loads(info_value)
    except (JSONDecodeError, TypeError):
        try:
            payload = json.loads(unescape(info_value))
        except (JSONDecodeError, TypeError):
            return None

    try:
        return parse_light_state(payload)
    except WiimWakeUpLightInvalidResponse:
        return None


def _first_text(parent: DET.Element, name: str) -> str | None:
    """Find the first namespace-agnostic element text."""
    for element in parent.iter():
        if element.tag.rsplit("}", 1)[-1] == name and element.text:
            value = element.text.strip()
            if value:
                return value
    return None


def parse_device_description(
    xml_text: str, status: dict[str, Any], host: str
) -> WiimWakeUpLightDescription:
    """Combine UPnP description and getStatusEx into stable device metadata."""
    try:
        root = DET.fromstring(xml_text)
    except (DET.ParseError, ValueError) as err:
        raise WiimWakeUpLightInvalidResponse("Invalid UPnP device description") from err

    project = status.get("project")
    api_version = status.get("LightApiVersion")
    if project != PROJECT_IDENTIFIER and not api_version:
        raise WiimWakeUpLightUnsupportedDevice(
            "LinkPlay device does not advertise WiiM Wake-up Light support"
        )

    description_url = f"http://{_url_host(host)}:{UPNP_PORT}{DESCRIPTION_PATH}"
    event_sub_url: str | None = None
    for service in root.iter():
        if service.tag.rsplit("}", 1)[-1] != "service":
            continue
        service_id = _first_text(service, "serviceId")
        service_type = _first_text(service, "serviceType")
        if service_id != LIGHT_SERVICE_ID and service_type != LIGHT_SERVICE_TYPE:
            continue
        event_path = _first_text(service, "eventSubURL")
        if event_path:
            candidate = urljoin(description_url, event_path)
            if urlparse(candidate).hostname == host:
                event_sub_url = candidate
        break

    # Some firmware may omit the service URL even while advertising LightApiVersion.
    if event_sub_url is None:
        event_sub_url = f"http://{_url_host(host)}:{UPNP_PORT}{LIGHT_EVENT_PATH}"

    udn = _first_text(root, "UDN") or _string_or_none(status.get("upnp_uuid"))
    raw_uuid = _string_or_none(status.get("uuid"))
    if not udn and raw_uuid:
        udn = f"uuid:{raw_uuid}"
    if not udn:
        raise WiimWakeUpLightInvalidResponse("Device did not provide a stable UDN")

    raw_uuid = raw_uuid or udn.removeprefix("uuid:")
    name = (
        _first_text(root, "friendlyName")
        or _string_or_none(status.get("DeviceName"))
        or "WiiM Wake-up Light"
    )
    manufacturer = _first_text(root, "manufacturer") or "WiiM"
    model = (
        _first_text(root, "modelName")
        or _first_text(root, "modelDescription")
        or "WiiM Wake-up Light"
    )

    return WiimWakeUpLightDescription(
        host=host,
        udn=udn,
        uuid=raw_uuid,
        name=name,
        manufacturer=manufacturer,
        model=model,
        firmware=_string_or_none(status.get("firmware")),
        light_api_version=_string_or_none(api_version),
        mac_address=_string_or_none(status.get("MAC")),
        event_sub_url=event_sub_url,
    )


def _string_or_none(value: Any) -> str | None:
    """Return a non-empty string or None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _url_host(host: str) -> str:
    """Bracket IPv6 literals when constructing a URL."""
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


class WiimWakeUpLightApi:
    """Asynchronous client for the unauthenticated local lamp API."""

    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        """Initialize the API client."""
        self._session = session
        self.host = host.strip().removesuffix(".")
        url_host = _url_host(self.host)
        self._command_url = f"https://{url_host}/httpapi.asp"
        self._post_url = f"https://{url_host}/httpapi.asp?path=wakeuplight"
        self.description_url = f"http://{url_host}:{UPNP_PORT}{DESCRIPTION_PATH}"

    async def async_get_light_info(self) -> WiimWakeUpLightState:
        """Fetch complete current lamp state."""
        payload = await self._async_get_json("getLightInfo")
        return parse_light_state(payload)

    async def async_get_status(self) -> dict[str, Any]:
        """Fetch device identity and capability metadata."""
        payload = await self._async_get_json("getStatusEx")
        if not isinstance(payload, dict):
            raise WiimWakeUpLightInvalidResponse("getStatusEx is not an object")
        if isinstance(payload.get("params"), dict):
            result_code = _coerce_int(payload.get("state"))
            if result_code != 0:
                raise WiimWakeUpLightInvalidResponse(
                    f"getStatusEx returned API result {payload.get('state')!r}"
                )
            return payload["params"]
        return payload

    async def async_get_description(self) -> str:
        """Fetch the UPnP device description."""
        return await self._async_request_text("GET", self.description_url)

    async def async_probe(self) -> WiimWakeUpLightDescription:
        """Validate a host and return its device description."""
        status, xml_text, _ = await asyncio.gather(
            self.async_get_status(),
            self.async_get_description(),
            self.async_get_light_info(),
        )
        return parse_device_description(xml_text, status, self.host)

    async def async_set_power(self, *, is_on: bool) -> None:
        """Turn the lamp on or off."""
        await self._async_post_command(f"setLightOn:{1 if is_on else 0}")

    async def async_set_brightness(self, percent: int) -> None:
        """Set brightness using the lamp's GET command."""
        await self._async_get_command(f"setLightBrightness:{_clamp(percent, 1, 100)}")

    async def async_set_rgb(self, rgb_color: tuple[int, int, int]) -> None:
        """Set RGB color, which also turns on and resets brightness to 100%."""
        await self._async_post_command(
            f"setLightDisplayColor:{format_rgb_hex(rgb_color)}"
        )

    async def async_set_effect(self, effect: str) -> None:
        """Enter one of the 15 built-in modes using companion-app defaults."""
        try:
            mode_id = EFFECTS[effect]
        except KeyError as err:
            raise ValueError(f"Unknown WiiM Wake-up Light effect: {effect}") from err
        await self._async_post_command(
            f"enterLightMode:{mode_id}:speed:{DEFAULT_MODE_SPEED}:"
            f"duration:{DEFAULT_MODE_DURATION}:style:{DEFAULT_MODE_STYLE}"
        )

    async def _async_get_command(self, command: str) -> None:
        """Send a command through the GET endpoint and validate its result."""
        payload = await self._async_get_json(command)
        self._validate_command_result(payload, command)

    async def _async_post_command(self, command: str) -> None:
        """Send a command through the wakeuplight POST endpoint."""
        # The firmware describes this endpoint as form encoded but does not decode
        # percent escapes. Passing a mapping to aiohttp changes ':' to '%3A' and
        # the lamp responds with plain text "unknown command". Send the simple
        # one-field body verbatim while retaining the expected content type.
        text = await self._async_request_text(
            "POST",
            self._post_url,
            data=f"command={command}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            payload = json.loads(text)
        except JSONDecodeError as err:
            raise WiimWakeUpLightInvalidResponse(
                f"Command {command} returned invalid JSON"
            ) from err
        self._validate_command_result(payload, command)

    async def _async_get_json(self, command: str) -> Any:
        """Fetch and explicitly decode JSON regardless of Content-Type."""
        text = await self._async_request_text(
            "GET", self._command_url, params={"command": command}
        )
        try:
            return json.loads(text)
        except JSONDecodeError as err:
            raise WiimWakeUpLightInvalidResponse(
                f"Command {command} returned invalid JSON"
            ) from err

    async def _async_request_text(self, method: str, url: str, **kwargs: Any) -> str:
        """Perform one local request with redirects disabled."""
        try:
            async with self._session.request(
                method,
                url,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
                **kwargs,
            ) as response:
                response.raise_for_status()
                return await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WiimWakeUpLightConnectionError(
                f"Unable to communicate with WiiM Wake-up Light at {self.host}"
            ) from err

    @staticmethod
    def _validate_command_result(payload: Any, command: str) -> None:
        """Validate the result code returned by a mutating command."""
        if not isinstance(payload, dict) or _coerce_int(payload.get("state")) != 0:
            raise WiimWakeUpLightCommandError(
                f"Command {command} failed with response {payload!r}"
            )
