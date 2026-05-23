"""Cookeo coordinator."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    AWS_CREDS_REFRESH_LEEWAY_S,
    CONF_APPLIANCE,
    CONF_PROFILE_PERIMETER,
    CONF_TOKENS,
    DOMAIN,
)
from .lib import (
    ApplianceInfo,
    AwsCredentials,
    CookeoShadow,
    CookeoStatus,
    IotClient,
    Tokens,
)
from .lib import cognito, oauth, seb_api

_LOGGER = logging.getLogger(__name__)


@dataclass
class CookeoData:
    """Snapshot exposed to entities. Mutated in-place on every update."""

    status: CookeoStatus = field(default_factory=CookeoStatus)
    shadow: CookeoShadow = field(default_factory=CookeoShadow)
    last_alert: dict | None = None


class CookeoCoordinator(DataUpdateCoordinator[CookeoData]):
    """Owns one appliance's MQTT subscription and credential rotations."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        appliance: ApplianceInfo,
        tokens: Tokens,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{appliance.thing_uuid[:8]}",
            update_interval=None,
        )
        self.entry = entry
        self.appliance = appliance
        self._tokens = tokens
        self._creds: AwsCredentials | None = None
        self._client: IotClient | None = None
        self._rotation_task: asyncio.Task[None] | None = None
        self.data = CookeoData()

    async def async_setup(self) -> None:
        session = async_get_clientsession(self.hass)
        self._tokens = await oauth.refresh(session, self._tokens.refresh_token or "")
        self._persist_tokens()

        await seb_api.attach_policies(
            session, self._tokens.id_token, self.appliance.thing_type
        )

        self._creds = await self.hass.async_add_executor_job(
            cognito.federate, self._tokens.id_token
        )

        self._client = IotClient(self.appliance)
        self._client.set_callbacks(
            on_status=self._handle_status_threadsafe,
            on_shadow=self._handle_shadow_threadsafe,
            on_alert=self._handle_alert_threadsafe,
        )
        await self.hass.async_add_executor_job(self._client.connect, self._creds)

        self._rotation_task = self.hass.async_create_background_task(
            self._rotation_loop(),
            name=f"{DOMAIN}:{self.appliance.thing_uuid[:8]}:rotation",
        )

    async def async_shutdown(self) -> None:
        if self._rotation_task and not self._rotation_task.done():
            self._rotation_task.cancel()
        if self._client is not None:
            await self.hass.async_add_executor_job(self._client.disconnect)
            self._client = None

    async def _rotation_loop(self) -> None:
        # AWS temp creds live ~1h. Refresh OAuth + Cognito just before
        # they expire, then reconnect MQTT with the new creds.
        session = async_get_clientsession(self.hass)
        while True:
            try:
                if self._creds is None:
                    await asyncio.sleep(60)
                    continue
                wait = (
                    self._creds.expiration
                    - int(time.time())
                    - AWS_CREDS_REFRESH_LEEWAY_S
                )
                await asyncio.sleep(max(60, wait))

                if not self._tokens.refresh_token:
                    _LOGGER.warning("no refresh_token cached; cannot rotate creds")
                    return
                self._tokens = await oauth.refresh(
                    session, self._tokens.refresh_token
                )
                self._persist_tokens()
                new_creds = await self.hass.async_add_executor_job(
                    cognito.federate, self._tokens.id_token
                )
                self._creds = new_creds
                if self._client is not None:
                    await self.hass.async_add_executor_job(
                        self._client.refresh_credentials, new_creds
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("rotation failed; will retry shortly")
                await asyncio.sleep(60)

    def _handle_status_threadsafe(self, status: CookeoStatus) -> None:
        # Called from awscrt's IO thread — marshal to the HA event loop.
        self.hass.loop.call_soon_threadsafe(self._handle_status, status)

    def _handle_shadow_threadsafe(self, shadow: CookeoShadow) -> None:
        self.hass.loop.call_soon_threadsafe(self._handle_shadow, shadow)

    def _handle_alert_threadsafe(self, alert: dict) -> None:
        self.hass.loop.call_soon_threadsafe(self._handle_alert, alert)

    @callback
    def _handle_status(self, status: CookeoStatus) -> None:
        self.data.status = self.data.status.merge(status)
        self.async_set_updated_data(self.data)

    @callback
    def _handle_shadow(self, shadow: CookeoShadow) -> None:
        # Shadow payloads often contain only the field that changed —
        # merge so a partial update doesn't reset ``connected`` to None.
        self.data.shadow = self.data.shadow.merge(shadow)
        self.async_set_updated_data(self.data)

    @callback
    def _handle_alert(self, alert: dict) -> None:
        self.data.last_alert = alert
        self.async_set_updated_data(self.data)

    def _persist_tokens(self) -> None:
        new_data = {
            **self.entry.data,
            CONF_TOKENS: {
                "access_token": self._tokens.access_token,
                "id_token": self._tokens.id_token,
                "refresh_token": self._tokens.refresh_token,
                "issued_at": self._tokens.issued_at,
                "id_token_exp": self._tokens.id_token_exp,
            },
        }
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
