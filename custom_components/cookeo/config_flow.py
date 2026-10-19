"""Config flow for Cookeo WiFi.

UX flow:

1. **user** — pick the brand/market and enter the Moulinex account
   email and password. The integration logs in on the user's behalf;
   the password is used once and never stored. Then refresh once to
   validate and fetch the user's appliances. If multiple Cookeos, ask
   which.
2. **pick** — multi-appliance disambiguation (when applicable).
3. **created** — config entry stored.
"""

from __future__ import annotations

import logging
from typing import Any

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

from .const import CONF_APPLIANCE, CONF_PROFILE_PERIMETER, CONF_TOKENS, DOMAIN
from .lib import ApplianceInfo, Tokens
from .lib import oauth, seb_api
from .lib.oauth import LoginError, OAuthError

_LOGGER = logging.getLogger(__name__)

#: Profile perimeters the integration knows how to OAuth against.
#: Keep ordered with the most common French/UK options first.
PERIMETERS: list[tuple[str, str, str, str]] = [
    # (perimeter slug used in expid_*, market, lang, human label)
    ("moulinex", "FR", "fr", "Moulinex / France"),
    ("tefal", "GB", "en", "Tefal / UK"),
    ("moulinex", "BE", "fr", "Moulinex / Belgique"),
    ("tefal", "DE", "de", "Tefal / Deutschland"),
    ("tefal", "IT", "it", "Tefal / Italia"),
    ("tefal", "ES", "es", "Tefal / España"),
]


class CookeoFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cookeo WiFi."""

    VERSION = 1

    def __init__(self) -> None:
        self._tokens: Tokens | None = None
        self._profile_perimeter: str | None = None
        self._appliances: list[ApplianceInfo] = []

    # ------------------------------------------------------------- step user

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the brand/market and log in with the account credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            perim_key = user_input["perimeter"]
            perim, market, lang, _label = next(
                p for p in PERIMETERS if f"{p[0]}-{p[1]}-{p[2]}" == perim_key
            )
            # We need the Salesforce profilePerimeter (e.g. ``MOULINEX_FR``)
            # to call SEB APIs later. Derive it the same way the official
            # app does.
            self._profile_perimeter = f"{perim.upper()}_{market.upper()}"
            try:
                self._tokens = await oauth.login(
                    user_input[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                    profile_perimeter=perim,
                    market=market,
                    lang=lang,
                )
            except LoginError as err:
                errors["base"] = err.reason
            except (OAuthError, aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.warning("SEB login failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_after_login(user_input)

        return self._async_show_user_form(user_input, errors)

    def _async_show_user_form(
        self, user_input: dict[str, Any] | None, errors: dict[str, str]
    ) -> ConfigFlowResult:
        schema = vol.Schema(
            {
                vol.Required("perimeter", default="moulinex-FR-fr"): vol.In(
                    {f"{p[0]}-{p[1]}-{p[2]}": p[3] for p in PERIMETERS}
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
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(
                schema, {k: v for k, v in user_input.items() if k != CONF_PASSWORD}
            )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def _async_after_login(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Common path once we have tokens: refresh, fetch profile."""
        assert self._tokens is not None
        session = async_get_clientsession(self.hass)

        try:
            # Validate the tokens with one refresh round-trip. Salesforce
            # rotates the ``id_token`` (back to a fresh ~2 min validity)
            # and sometimes the ``refresh_token``.
            self._tokens = await oauth.refresh(session, self._tokens.refresh_token or "")
            profile = await seb_api.fetch_profile(
                session, self._tokens.access_token, self._profile_perimeter or "MOULINEX_FR"
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("post-login SEB API failed: %s", err)
            return self._async_show_user_form(user_input, {"base": "seb_api_failed"})

        # Pull profilePerimeter from the response — it's authoritative.
        self._profile_perimeter = profile.get("profilePerimeter") or self._profile_perimeter
        self._appliances = seb_api.appliances_from_profile(profile)

        if not self._appliances:
            return self.async_abort(reason="no_appliances")
        if len(self._appliances) == 1:
            return await self._async_create(self._appliances[0])
        return await self.async_step_pick()

    # ------------------------------------------------------------- step pick

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Disambiguate between multiple Cookeos on the account."""
        if user_input is not None:
            uuid = user_input["appliance"]
            appliance = next(a for a in self._appliances if a.thing_uuid == uuid)
            return await self._async_create(appliance)
        choices = {
            a.thing_uuid: f"{a.name} ({a.thing_uuid[:8]}…)" for a in self._appliances
        }
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema({vol.Required("appliance"): vol.In(choices)}),
        )

    async def _async_create(self, appliance: ApplianceInfo) -> ConfigFlowResult:
        await self.async_set_unique_id(appliance.thing_uuid)
        self._abort_if_unique_id_configured()
        assert self._tokens is not None
        return self.async_create_entry(
            title=appliance.name or f"Cookeo {appliance.thing_uuid[:8]}",
            data={
                CONF_TOKENS: {
                    "access_token": self._tokens.access_token,
                    "id_token": self._tokens.id_token,
                    "refresh_token": self._tokens.refresh_token,
                    "issued_at": self._tokens.issued_at,
                    "id_token_exp": self._tokens.id_token_exp,
                },
                CONF_PROFILE_PERIMETER: self._profile_perimeter,
                CONF_APPLIANCE: {
                    "appliance_id": appliance.appliance_id,
                    "name": appliance.name,
                    "thing_type": appliance.thing_type,
                    "thing_uuid": appliance.thing_uuid,
                    "functional_id": appliance.functional_id,
                    "rcu_asset_id": appliance.rcu_asset_id,
                    "cmmf": appliance.cmmf,
                },
            },
        )
