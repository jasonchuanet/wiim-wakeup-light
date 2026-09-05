from __future__ import annotations

import pytest

from custom_components.wiim_wakeup_light.api import (
    WiimWakeUpLightInvalidResponse,
    WiimWakeUpLightUnsupportedDevice,
    parse_device_description,
)

DESCRIPTION_XML = """\
<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <friendlyName>Bedroom</friendlyName>
    <manufacturer>Linkplay Technology Inc.</manufacturer>
    <modelName>WiiM Wake-up Light</modelName>
    <UDN>uuid:FF97002D-20DA-E3AD-E9C2-777AFF97002D</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-wiimu-com:service:LightManager:1</serviceType>
        <serviceId>urn:wiimu-com:serviceId:LightManager</serviceId>
        <SCPDURL>/upnp/LightMangerSCPD.xml</SCPDURL>
        <controlURL>/upnp/control/LightManager1</controlURL>
        <eventSubURL>/upnp/event/LightManager1</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>
"""

STATUS = {
    "project": "WiiM_Light",
    "firmware": "Linkplay.4.6.824755",
    "uuid": "FF97002D20DAE3ADE9C2777A",
    "MAC": "B8:13:32:D7:63:B4",
    "LightApiVersion": "1.2",
}


def test_parse_live_device_identity_and_event_url() -> None:
    description = parse_device_description(DESCRIPTION_XML, STATUS, "192.168.0.50")

    assert description.name == "Bedroom"
    assert description.udn == "uuid:FF97002D-20DA-E3AD-E9C2-777AFF97002D"
    assert description.model == "WiiM Wake-up Light"
    assert description.event_sub_url == (
        "http://192.168.0.50:49152/upnp/event/LightManager1"
    )


def test_known_event_url_is_fallback_when_service_is_missing() -> None:
    description = parse_device_description(
        "<root><device><UDN>uuid:test</UDN></device></root>",
        {"LightApiVersion": "1.2"},
        "192.0.2.2",
    )

    assert description.event_sub_url.endswith("/upnp/event/LightManager1")


def test_unrelated_linkplay_device_is_rejected() -> None:
    with pytest.raises(WiimWakeUpLightUnsupportedDevice):
        parse_device_description(DESCRIPTION_XML, {"project": "WiiM_Pro"}, "192.0.2.3")


def test_invalid_xml_is_rejected() -> None:
    with pytest.raises(WiimWakeUpLightInvalidResponse):
        parse_device_description("<root>", STATUS, "192.0.2.4")
