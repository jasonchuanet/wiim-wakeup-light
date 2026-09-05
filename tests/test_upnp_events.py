from __future__ import annotations

from html import escape

from custom_components.wiim_wakeup_light.api import parse_last_change


def test_empty_initial_last_change_is_ignored() -> None:
    value = (
        '<Event xmlns="urn:schemas-wiimu-com:light-1-0/LightManager/">'
        "<LightManager /></Event>"
    )
    assert parse_last_change(value) is None


def test_doubly_escaped_real_light_event() -> None:
    nested = """
    <Event xmlns="urn:schemas-wiimu-com:light-1-0/LightManager/">
      <LightManager>
        <change val="light"/>
        <info val='{ &quot;light_on&quot;: 1,
                     &quot;light_color_RGB&quot;: {
                       &quot;R&quot;: 255, &quot;G&quot;: 255, &quot;B&quot;: 255 },
                     &quot;light_mode&quot;: &quot;Restore&quot;,
                     &quot;light_brig&quot;: 34,
                     &quot;light_sleep_mode&quot;: 0,
                     &quot;light_reading_routine&quot;: 0,
                     &quot;light_clock_display&quot;: 0 }'/>
      </LightManager>
    </Event>
    """

    state = parse_last_change(escape(nested))

    assert state is not None
    assert state.is_on is True
    assert state.brightness_percent == 34
    assert state.rgb_color == (255, 255, 255)
    assert state.effect == "Restore"


def test_async_upnp_client_decoded_event_with_double_quoted_info() -> None:
    """Parse the exact attribute quoting delivered by async-upnp-client."""
    value = """
    <Event xmlns="urn:schemas-wiimu-com:light-1-0/LightManager/">
      <LightManager>
        <change val="light"/>
        <info val="{ &quot;light_on&quot;: 1,
                     &quot;light_color_RGB&quot;: {
                       &quot;R&quot;: 255, &quot;G&quot;: 255, &quot;B&quot;: 255 },
                     &quot;light_mode&quot;: &quot;My favorite&quot;,
                     &quot;light_brig&quot;: 38,
                     &quot;light_sleep_mode&quot;: 0,
                     &quot;light_reading_routine&quot;: 0,
                     &quot;light_clock_display&quot;: 0 }"/>
      </LightManager>
    </Event>
    """

    state = parse_last_change(value)

    assert state is not None
    assert state.is_on is True
    assert state.brightness_percent == 38
    assert state.rgb_color == (255, 255, 255)
    assert state.effect == "My favorite"


def test_unrelated_upnp_change_is_ignored() -> None:
    value = """
    <Event>
      <LightManager>
        <change val="player"/>
        <info val='{ &quot;light_on&quot;: 1 }'/>
      </LightManager>
    </Event>
    """
    assert parse_last_change(value) is None


def test_malformed_nested_json_is_nonfatal() -> None:
    value = '<Event><change val="light"/><info val="not-json"/></Event>'
    assert parse_last_change(value) is None
