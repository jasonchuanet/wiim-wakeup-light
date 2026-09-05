# WiiM Wake-up Light for Home Assistant

This HACS custom integration exposes the lamp in a **WiiM Wake-up Light** as a
native Home Assistant `light` entity. It is deliberately focused on lighting:
it does not add another media player or change alarms, routines, playback,
sleep timers, or clock settings.

## Features

- Local-only power, brightness, and RGB control.
- All 15 built-in light modes exposed as effects.
- Local UPnP `LightManager:1` push updates for physical and companion-app
  changes.
- A conservative 60-second poll for availability, reboot recovery, and
  operation when the callback is unreachable.
- Automatic `_linkplay._tcp.local.` discovery plus manual host entry.
- Stable UDN-based config and entity IDs, with discovered address updates.
- Device-registry association with the official Home Assistant WiiM
  integration, so the light joins the existing WiiM device card when present.
- Diagnostic output with host, UDN, UUID, and MAC address redacted.

## Installation

### HACS custom repository

After this directory is published as its own GitHub repository:

1. Open HACS in Home Assistant.
2. Add the repository URL as a custom repository in the **Integration**
   category.
3. Install **WiiM Wake-up Light** and restart Home Assistant.

### Manual

Copy `custom_components/wiim_wakeup_light` into the `custom_components`
directory under your Home Assistant configuration directory, then restart
Home Assistant.

## Configuration

Open **Settings → Devices & services → Add integration**, select
**WiiM Wake-up Light**, and enter the lamp's local IP address or host name. A
compatible Zeroconf discovery can also prompt for confirmation automatically.

The flow verifies all of the following before creating an entry:

- `getStatusEx` advertises `project: WiiM_Light` or `LightApiVersion`.
- The local HTTPS light API returns a valid `getLightInfo` response.
- The UPnP device description supplies a stable UDN.

The light API uses HTTPS with the lamp's self-signed certificate. Certificate
verification is disabled only on the Home Assistant client session selected
for this local device API; no cloud credentials or authentication are used.

## Behavior notes

The lamp resets brightness to 100% whenever RGB is set. To preserve the
requested final brightness, this integration always sends effect first, RGB
second, and brightness last. Turning the light off sends only `setLightOn:0`.

UPnP callbacks use a small local HTTP listener selected from Home Assistant's
route to the lamp. Multiple lamps on the same Home Assistant network interface
share one callback listener. Subscription renewal occurs before the device's
reported expiry; a failed renewal is retried without turning an otherwise
pollable light unavailable.

## Supported effects

Reading, Night, Unwind, Energize, Relax, Restore, Rainbow, Fireworks, Deep
Ocean, Forest, Music, Meditation, My favorite, Sunrise, and Sunset.

The companion-app defaults `speed:10`, `duration:1`, and `style:1` are used for
every effect because the accepted ranges and semantics are not yet documented.

## Development

The test suite covers payload tolerance, result-code semantics, brightness and
RGB conversions, command ordering, effect IDs, real nested `LastChange`
parsing, device identification, subscription renewal, failed renewal, retry,
and unload cleanup.

```bash
uv run --group test pytest
uv run --group test ruff check .
```

Hassfest and HACS validation workflows are included. Brand assets are ignored
by the HACS workflow until this integration is published and added to the Home
Assistant brands repository.

## Scope and known unknowns

This first release intentionally does not expose alarm, routine, clock,
ambient-light, sleep-timer, or media-player commands. Firmware behavior at an
incoming brightness value of zero, and the exact effect tuning parameter
ranges, still need broader device validation.

The protocol implementation is based on owner-authorized local testing and
companion-app inspection summarized in the project handoff document; it is not
an official published WiiM API.

## License

MIT
