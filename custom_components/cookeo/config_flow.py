"""Config flow for the Cookeo integration."""

from dataclasses import asdict
from typing import Any, NamedTuple

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_PROFILE_PERIMETER,
    CONF_REGION,
    CONF_TOKENS,
    DOMAIN,
    LOGGER,
)
from .lib import CookeoError, LoginError, oauth, seb_api


class Region(NamedTuple):
    """Brand and market of a Moulinex account."""

    brand: str
    market: str
    language: str
    label: str


REGIONS: dict[str, Region] = {
    "MOULINEX_FR": Region("moulinex", "FR", "fr", "Moulinex / France"),
    "TEFAL_GB": Region("tefal", "GB", "en", "Tefal / UK"),
    "MOULINEX_BE": Region("moulinex", "BE", "fr", "Moulinex / Belgique"),
    "TEFAL_DE": Region("tefal", "DE", "de", "Tefal / Deutschland"),
    "TEFAL_IT": Region("tefal", "IT", "it", "Tefal / Italia"),
    "TEFAL_ES": Region("tefal", "ES", "es", "Tefal / España"),
}

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REGION, default="MOULINEX_FR"): vol.In(
            {key: region.label for key, region in REGIONS.items()}
        ),
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


class CookeoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cookeo."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in with the Moulinex account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            perimeter = user_input[CONF_REGION]
            region = REGIONS[perimeter]
            try:
                tokens = await oauth.login(
                    user_input[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                    brand=region.brand,
                    market=region.market,
                    language=region.language,
                )
                profile = await seb_api.fetch_profile(
                    async_get_clientsession(self.hass), tokens.access_token, perimeter
                )
            except LoginError as err:
                errors["base"] = err.reason
            except (CookeoError, aiohttp.ClientError, TimeoutError) as err:
                LOGGER.warning("Could not connect to the SEB cloud: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                if not seb_api.appliances_from_profile(profile):
                    return self.async_abort(reason="no_appliances")
                await self.async_set_unique_id(tokens.user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL],
                    data={
                        CONF_TOKENS: asdict(tokens),
                        CONF_PROFILE_PERIMETER: profile.get(
                            "profilePerimeter", perimeter
                        ),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA,
                {k: v for k, v in (user_input or {}).items() if k != CONF_PASSWORD},
            ),
            errors=errors,
        )
