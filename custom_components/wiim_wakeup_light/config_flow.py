"""Config flow for the WiiM Wake-up Light integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    WiimWakeUpLightApi,
    WiimWakeUpLightConnectionError,
    WiimWakeUpLightInvalidResponse,
    WiimWakeUpLightUnsupportedDevice,
)
from .const import DOMAIN
from .models import WiimWakeUpLightDescription

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class WiimWakeUpLightConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle configuration for a WiiM Wake-up Light."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered: WiimWakeUpLightDescription | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual host entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _normalize_host(user_input[CONF_HOST])
            if not _valid_host_input(host):
                errors[CONF_HOST] = "invalid_host"
            else:
                try:
                    description = await self._async_probe(host)
                except WiimWakeUpLightConnectionError:
                    errors["base"] = "cannot_connect"
                except WiimWakeUpLightUnsupportedDevice:
                    errors["base"] = "not_supported"
                except WiimWakeUpLightInvalidResponse:
                    errors["base"] = "invalid_response"
                else:
                    await self.async_set_unique_id(description.udn)
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: description.host}
                    )
                    return self.async_create_entry(
                        title=description.name,
                        data={CONF_HOST: description.host},
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Probe LinkPlay discovery and ignore unrelated audio products."""
        host = _normalize_host(discovery_info.host)
        try:
            description = await self._async_probe(host)
        except WiimWakeUpLightUnsupportedDevice:
            return self.async_abort(reason="not_supported")
        except (WiimWakeUpLightConnectionError, WiimWakeUpLightInvalidResponse):
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(description.udn)
        self._abort_if_unique_id_configured(updates={CONF_HOST: description.host})
        self._discovered = description
        self.context["title_placeholders"] = {"name": description.name}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm a compatible discovered lamp."""
        if user_input is not None and self._discovered is not None:
            return self.async_create_entry(
                title=self._discovered.name,
                data={CONF_HOST: self._discovered.host},
            )
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={
                "name": self._discovered.name
                if self._discovered is not None
                else "WiiM Wake-up Light"
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow a host to be corrected without replacing the entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _normalize_host(user_input[CONF_HOST])
            if not _valid_host_input(host):
                errors[CONF_HOST] = "invalid_host"
            else:
                try:
                    description = await self._async_probe(host)
                except WiimWakeUpLightConnectionError:
                    errors["base"] = "cannot_connect"
                except WiimWakeUpLightUnsupportedDevice:
                    errors["base"] = "not_supported"
                except WiimWakeUpLightInvalidResponse:
                    errors["base"] = "invalid_response"
                else:
                    await self.async_set_unique_id(description.udn)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_HOST: description.host},
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA,
                user_input or {CONF_HOST: entry.data[CONF_HOST]},
            ),
            errors=errors,
        )

    async def _async_probe(self, host: str) -> WiimWakeUpLightDescription:
        """Probe one host with the session that accepts only this local TLS device."""
        session = async_get_clientsession(self.hass, verify_ssl=False)
        return await WiimWakeUpLightApi(session, host).async_probe()


def _normalize_host(host: str) -> str:
    """Normalize a manual or Zeroconf host value."""
    return host.strip().strip("[]").removesuffix(".")


def _valid_host_input(host: str) -> bool:
    """Reject URLs and path-like values; the flow accepts a host only."""
    return bool(host) and "://" not in host and "/" not in host and " " not in host
