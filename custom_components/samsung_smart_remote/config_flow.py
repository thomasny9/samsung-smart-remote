"""Config flow for Samsung Smart Remote."""

from __future__ import annotations

import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN

CONF_WOL_BROADCAST = "wol_broadcast_address"


class SamsungSmartRemoteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure Samsung Smart Remote."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Samsung Smart Remote", data={})
        return self.async_show_form(step_id="user", data_schema=None)

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SamsungSmartRemoteOptionsFlow:
        return SamsungSmartRemoteOptionsFlow()


class SamsungSmartRemoteOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Samsung Smart Remote."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            value = str(user_input.get(CONF_WOL_BROADCAST, "")).strip()
            if value:
                try:
                    ipaddress.IPv4Address(value)
                except ipaddress.AddressValueError:
                    errors[CONF_WOL_BROADCAST] = "invalid_ipv4"
                else:
                    return self.async_create_entry(
                        title="",
                        data={CONF_WOL_BROADCAST: value},
                    )
            else:
                return self.async_create_entry(title="", data={})

        current = self.config_entry.options.get(CONF_WOL_BROADCAST, "")
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_WOL_BROADCAST,
                    default=current,
                ): str,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
