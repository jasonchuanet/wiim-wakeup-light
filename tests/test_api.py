from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from custom_components.wiim_wakeup_light.api import (
    CommandKind,
    WiimWakeUpLightApi,
    WiimWakeUpLightCommandError,
    WiimWakeUpLightInvalidResponse,
    brightness_ha_to_percent,
    brightness_percent_to_ha,
    build_turn_on_commands,
    format_rgb_hex,
    parse_light_state,
)


class FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def text(self) -> str:
        return self._body


class FakeSession:
    def __init__(self, *responses: str) -> None:
        self._responses: Iterator[str] = iter(responses)
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((method, url, kwargs))
        return FakeResponse(next(self._responses))


def test_parse_get_light_info_distinguishes_result_from_power() -> None:
    state = parse_light_state(
        {
            "state": 0,
            "params": {
                "light_on": 1,
                "light_brig": 33,
                "light_color_RGB": {"R": 255, "G": 159, "B": 122},
                "light_mode": "Restore",
            },
        }
    )

    assert state.is_on is True
    assert state.brightness_percent == 33
    assert state.rgb_color == (255, 159, 122)
    assert state.effect == "Restore"


def test_parse_tolerates_string_numbers_unknown_and_missing_optional_keys() -> None:
    state = parse_light_state(
        {
            "light_on": "0",
            "light_brig": "52",
            "light_color_RGB": {"R": "1", "G": "02", "B": "255"},
            "new_firmware_field": {"anything": True},
        }
    )

    assert state.is_on is False
    assert state.brightness_percent == 52
    assert state.rgb_color == (1, 2, 255)
    assert state.effect is None
    assert state.sleep_mode is None


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(0, 0), (1, 3), (50, 128), (100, 255), (120, 255)],
)
def test_brightness_percent_to_ha(percent: int, expected: int) -> None:
    assert brightness_percent_to_ha(percent) == expected


@pytest.mark.parametrize(
    ("brightness", "expected"),
    [(0, 1), (1, 1), (128, 50), (254, 100), (255, 100), (300, 100)],
)
def test_brightness_ha_to_percent(brightness: int, expected: int) -> None:
    assert brightness_ha_to_percent(brightness) == expected


def test_rgb_is_zero_padded_lowercase_hex() -> None:
    assert format_rgb_hex((0, 1, 255)) == "0001ff"


def test_rgb_validation() -> None:
    with pytest.raises(ValueError):
        format_rgb_hex((0, -1, 256))


def test_turn_on_command_order_preserves_final_brightness() -> None:
    commands = build_turn_on_commands(
        effect="Sunrise", rgb_color=(255, 159, 122), brightness=128
    )

    assert [command.kind for command in commands] == [
        CommandKind.EFFECT,
        CommandKind.RGB,
        CommandKind.BRIGHTNESS,
    ]
    assert commands[-1].value == 50


def test_plain_turn_on_uses_power_command() -> None:
    assert build_turn_on_commands()[0].kind is CommandKind.POWER


@pytest.mark.asyncio
async def test_effect_and_rgb_wire_commands_keep_literal_colons() -> None:
    session = FakeSession('{"state": 0}', '{"state": "0"}')
    api = WiimWakeUpLightApi(session, "192.0.2.1")  # type: ignore[arg-type]

    await api.async_set_effect("Sunrise")
    await api.async_set_rgb((0, 1, 255))

    assert session.requests[0][0] == "POST"
    assert session.requests[0][2]["data"] == (
        "command=enterLightMode:14:speed:10:duration:1:style:1"
    )
    assert session.requests[0][2]["headers"] == {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    assert session.requests[1][2]["data"] == (
        "command=setLightDisplayColor:0001ff"
    )


@pytest.mark.asyncio
async def test_invalid_json_is_reported_without_content_type_dependency() -> None:
    api = WiimWakeUpLightApi(FakeSession("not json"), "192.0.2.1")  # type: ignore[arg-type]

    with pytest.raises(WiimWakeUpLightInvalidResponse):
        await api.async_get_light_info()


@pytest.mark.asyncio
async def test_nonzero_command_result_is_an_error() -> None:
    api = WiimWakeUpLightApi(FakeSession('{"state": 5}'), "192.0.2.1")  # type: ignore[arg-type]

    with pytest.raises(WiimWakeUpLightCommandError):
        await api.async_set_power(is_on=False)
