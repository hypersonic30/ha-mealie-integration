"""Config flow for the Mealie Hub integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import MealieClient, UpstreamError
from .const import (
    CONF_MEALIE_API_KEY,
    CONF_MEALIE_URL,
    CONF_SOCIAL_URL,
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_URL = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


def _schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_MEALIE_URL, default=defaults.get(CONF_MEALIE_URL, "")): _URL,
        vol.Required(CONF_MEALIE_API_KEY, default=defaults.get(CONF_MEALIE_API_KEY, "")): _PASSWORD,
        vol.Optional(CONF_SOCIAL_URL, description={"suggested_value": defaults.get(CONF_SOCIAL_URL, "")}): _URL,
        vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): bool,
    })


async def _validate(hass, data: dict) -> str | None:
    """Returns an error code, or None if Mealie accepted the key."""
    client = MealieClient(hass, data[CONF_MEALIE_URL], data[CONF_MEALIE_API_KEY], data.get(CONF_VERIFY_SSL, True))
    try:
        await client.list_recipes(page=1, per_page=1)
    except UpstreamError as err:
        return "invalid_auth" if err.status in (401, 403) else "cannot_connect"
    except Exception:  # noqa: BLE001 - any transport failure means "can't reach it"
        return "cannot_connect"
    return None


class MealieHubConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _validate(self.hass, user_input)
            if error is None:
                return self.async_create_entry(title="Mealie Hub", data=user_input)
            errors["base"] = error
        return self.async_show_form(step_id="user", data_schema=_schema(user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _validate(self.hass, user_input)
            if error is None:
                return self.async_update_reload_and_abort(entry, data=user_input)
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or entry.data),
            errors=errors,
        )
