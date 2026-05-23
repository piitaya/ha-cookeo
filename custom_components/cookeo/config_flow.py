"""Config flow for Cookeo WiFi.

UX flow:

1. **start** — explain what's going to happen, show the auth URL.
   The user opens it in a browser, logs in with their Moulinex account.
   After login, Salesforce redirects to ``gsmoduser://tokens#…`` —
   the browser shows a "no app to open this URL" error but the address
   bar contains the tokens. The user pastes that URL back.
2. **paste** — extract the tokens, refresh once to validate, federate
   Cognito, fetch the user's appliances. If multiple Cookeos, ask which.
3. **pick** — multi-appliance disambiguation (when applicable).
4. **created** — config entry stored.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_APPLIANCE, CONF_PROFILE_PERIMETER, CONF_TOKENS, DOMAIN
from .lib import ApplianceInfo, SF_REDIRECT_URI, Tokens
from .lib import oauth, seb_api
from .lib.oauth import OAuthError

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
        self._state: str | None = None
        self._tokens: Tokens | None = None
        self._profile_perimeter: str | None = None
        self._appliances: list[ApplianceInfo] = []
        self._auth_url: str | None = None
        self._auth_url_state: str | None = None

    # ------------------------------------------------------------- step user

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Initial step — pick the brand/market and show the auth URL."""
        if user_input is not None:
            perim_key = user_input["perimeter"]
            perim, market, lang, _label = next(
                p for p in PERIMETERS if f"{p[0]}-{p[1]}-{p[2]}" == perim_key
            )
            self._auth_url, self._auth_url_state = oauth.build_authorize_url(
                profile_perimeter=perim, market=market, lang=lang
            )
            # We need the Salesforce profilePerimeter (e.g. ``MOULINEX_FR``)
            # to call SEB APIs later. Derive it the same way the official
            # app does.
            self._profile_perimeter = f"{perim.upper()}_{market.upper()}"
            return await self.async_step_paste()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("perimeter", default="moulinex-FR-fr"): vol.In(
                        {f"{p[0]}-{p[1]}-{p[2]}": p[3] for p in PERIMETERS}
                    )
                }
            ),
        )

    # ------------------------------------------------------------ step paste

    async def async_step_paste(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the auth URL + ask the user to paste the redirect URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            raw = user_input.get("redirect_url", "").strip()
            try:
                self._tokens = oauth.parse_redirect(raw, self._auth_url_state)
            except OAuthError as err:
                _LOGGER.debug("paste step OAuth parse failed: %s", err)
                errors["base"] = "invalid_redirect"
            else:
                return await self._async_after_login()

        assert self._auth_url is not None
        return self.async_show_form(
            step_id="paste",
            data_schema=vol.Schema({vol.Required("redirect_url"): str}),
            description_placeholders={
                "auth_url": self._auth_url,
                "redirect_scheme": SF_REDIRECT_URI,
            },
            errors=errors,
        )

    async def _async_after_login(self) -> ConfigFlowResult:
        """Common path once we have tokens: refresh, attach policy, fetch profile."""
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
            return self.async_show_form(
                step_id="paste",
                data_schema=vol.Schema({vol.Required("redirect_url"): str}),
                description_placeholders={"auth_url": self._auth_url or ""},
                errors={"base": "seb_api_failed"},
            )

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
