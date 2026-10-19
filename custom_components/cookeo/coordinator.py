"""Coordinator for the Cookeo integration."""

import asyncio
from dataclasses import asdict, dataclass, field
from functools import partial
import time
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_APPLIANCE,
    CONF_TOKENS,
    CREDENTIALS_RENEWAL_MARGIN,
    DOMAIN,
    LOGGER,
)
from .lib import (
    Appliance,
    CookeoShadow,
    CookeoStatus,
    IotClient,
    Tokens,
    cognito,
    oauth,
    seb_api,
)

type CookeoConfigEntry = ConfigEntry[CookeoCoordinator]


@dataclass
class CookeoData:
    """Latest cooking status and shadow of a Cookeo."""

    status: CookeoStatus = field(default_factory=CookeoStatus)
    shadow: CookeoShadow = field(default_factory=CookeoShadow)


class CookeoCoordinator(DataUpdateCoordinator[CookeoData]):
    """Coordinator that pushes the MQTT updates of one Cookeo."""

    config_entry: CookeoConfigEntry

    def __init__(self, hass: HomeAssistant, entry: CookeoConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, LOGGER, config_entry=entry, name=DOMAIN)
        appliance = entry.data[CONF_APPLIANCE]
        self.appliance = Appliance(
            name=appliance["name"],
            thing_type=appliance["thing_type"],
            thing_uuid=appliance["thing_uuid"],
            cmmf=appliance.get("cmmf"),
        )
        tokens = entry.data[CONF_TOKENS]
        self._tokens = Tokens(
            access_token=tokens["access_token"],
            id_token=tokens["id_token"],
            refresh_token=tokens["refresh_token"],
        )
        self._client = IotClient(
            self.appliance,
            on_status=partial(hass.loop.call_soon_threadsafe, self._async_on_status),
            on_shadow=partial(hass.loop.call_soon_threadsafe, self._async_on_shadow),
        )
        self.data = CookeoData()

    async def async_connect(self) -> None:
        """Connect to AWS IoT and keep the credentials renewed."""
        await self._async_refresh_tokens()
        await seb_api.attach_policies(
            async_get_clientsession(self.hass),
            self._tokens.id_token,
            self.appliance.thing_type,
        )
        credentials = await self.hass.async_add_executor_job(
            cognito.federate, self._tokens.id_token
        )
        await self.hass.async_add_executor_job(self._client.connect, credentials)
        self.config_entry.async_create_background_task(
            self.hass,
            self._async_renew_credentials(credentials.expiration),
            name=f"{DOMAIN} credentials renewal",
        )

    @override
    async def async_shutdown(self) -> None:
        """Disconnect from AWS IoT."""
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self._client.disconnect)

    async def _async_renew_credentials(self, expiration: int) -> None:
        """Renew the AWS credentials shortly before they expire."""
        while True:
            await asyncio.sleep(
                max(60, expiration - time.time() - CREDENTIALS_RENEWAL_MARGIN)
            )
            try:
                await self._async_refresh_tokens()
                credentials = await self.hass.async_add_executor_job(
                    cognito.federate, self._tokens.id_token
                )
                await self.hass.async_add_executor_job(
                    self._client.refresh_credentials, credentials
                )
            except Exception:  # noqa: BLE001
                LOGGER.exception("Could not renew the AWS IoT credentials")
            else:
                expiration = credentials.expiration

    async def _async_refresh_tokens(self) -> None:
        """Refresh the SEB tokens and store them in the config entry."""
        self._tokens = await oauth.refresh(
            async_get_clientsession(self.hass), self._tokens.refresh_token
        )
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_TOKENS: asdict(self._tokens)},
        )

    @callback
    def _async_on_status(self, status: CookeoStatus) -> None:
        """Handle a cooking status update."""
        self.data.status = self.data.status.merge(status)
        self.async_set_updated_data(self.data)

    @callback
    def _async_on_shadow(self, shadow: CookeoShadow) -> None:
        """Handle a shadow update."""
        previous = self.data.shadow
        self.data.shadow = previous.merge(shadow)
        if previous.connected is False and self.data.shadow.connected:
            self._client.request_status()
        if self.data.shadow.firmware.ui_version != previous.firmware.ui_version:
            self._async_update_firmware()
        self.async_set_updated_data(self.data)

    @callback
    def _async_update_firmware(self) -> None:
        """Show the new firmware version on the device page."""
        device_registry = dr.async_get(self.hass)
        if device := device_registry.async_get_device(
            identifiers={(DOMAIN, self.appliance.thing_uuid)}
        ):
            device_registry.async_update_device(
                device.id, sw_version=self.data.shadow.firmware.ui_version
            )
