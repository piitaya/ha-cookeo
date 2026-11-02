"""Coordinators for the Cookeo integration."""

from dataclasses import asdict, dataclass, field
from datetime import timedelta
from functools import partial
import time
from typing import override

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_PROFILE_PERIMETER,
    CONF_TOKENS,
    CREDENTIALS_RENEWAL_MARGIN,
    DOMAIN,
    LOGGER,
    RETRY_INTERVAL,
)
from .lib import (
    Appliance,
    CookeoError,
    CookeoShadow,
    CookeoStatus,
    IotClient,
    Tokens,
    cognito,
    oauth,
    seb_api,
)


def tokens_from_data(data: dict[str, str]) -> Tokens:
    """Return the tokens stored in a config entry."""
    return Tokens(
        access_token=data["access_token"],
        id_token=data["id_token"],
        refresh_token=data["refresh_token"],
    )


type CookeoConfigEntry = ConfigEntry[CookeoAccountCoordinator]


@dataclass
class CookeoData:
    """Latest cooking status and shadow of an appliance."""

    status: CookeoStatus = field(default_factory=CookeoStatus)
    shadow: CookeoShadow = field(default_factory=CookeoShadow)


class CookeoAccountCoordinator(DataUpdateCoordinator[dict[str, Appliance]]):
    """Coordinator that keeps the account connected and its appliances up to date."""

    config_entry: CookeoConfigEntry

    def __init__(self, hass: HomeAssistant, entry: CookeoConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, LOGGER, config_entry=entry, name=DOMAIN)
        self._tokens = tokens_from_data(entry.data[CONF_TOKENS])
        self._client = IotClient(
            on_status=partial(hass.loop.call_soon_threadsafe, self._async_on_status),
            on_shadow=partial(hass.loop.call_soon_threadsafe, self._async_on_shadow),
        )
        self._thing_types: set[str] = set()
        self.devices: dict[str, CookeoCoordinator] = {}

    @override
    async def _async_update_data(self) -> dict[str, Appliance]:
        """Renew the credentials, refresh the appliances and reconnect to AWS IoT."""
        session = async_get_clientsession(self.hass)
        try:
            await self._async_refresh_tokens()
            profile = await seb_api.fetch_profile(
                session,
                self._tokens.access_token,
                self.config_entry.data[CONF_PROFILE_PERIMETER],
            )
            appliances = {
                appliance.thing_uuid: appliance
                for appliance in seb_api.appliances_from_profile(profile)
            }
            for thing_type in {a.thing_type for a in appliances.values()}:
                if thing_type not in self._thing_types:
                    await seb_api.attach_policies(
                        session, self._tokens.id_token, thing_type
                    )
                    self._thing_types.add(thing_type)
            credentials = await self.hass.async_add_executor_job(
                cognito.federate, self._tokens.id_token
            )
            self._async_add_devices(appliances)
            await self.hass.async_add_executor_job(
                self._client.reconnect, credentials, list(appliances.values())
            )
        except (CookeoError, aiohttp.ClientError, TimeoutError) as err:
            self.update_interval = RETRY_INTERVAL
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="connection_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self.update_interval = timedelta(
            seconds=max(
                RETRY_INTERVAL.total_seconds(),
                credentials.expiration - time.time() - CREDENTIALS_RENEWAL_MARGIN,
            )
        )
        self._async_remove_stale_devices(appliances)
        return appliances

    @override
    async def async_shutdown(self) -> None:
        """Disconnect from AWS IoT."""
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self._client.disconnect)

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
    def _async_add_devices(self, appliances: dict[str, Appliance]) -> None:
        """Create the coordinators of the new appliances before they send updates."""
        for uuid, appliance in appliances.items():
            if uuid not in self.devices:
                self.devices[uuid] = CookeoCoordinator(
                    self.hass, self.config_entry, appliance, self._client
                )

    @callback
    def _async_remove_stale_devices(self, appliances: dict[str, Appliance]) -> None:
        """Remove the appliances that are no longer linked to the account."""
        if not appliances:
            return
        for uuid in self.devices.keys() - appliances.keys():
            del self.devices[uuid]
        device_registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(
            device_registry, self.config_entry.entry_id
        ):
            if not any(
                domain == DOMAIN and uuid in appliances
                for domain, uuid in device.identifiers
            ):
                device_registry.async_update_device(
                    device.id, remove_config_entry_id=self.config_entry.entry_id
                )

    @callback
    def _async_on_status(self, appliance: Appliance, status: CookeoStatus) -> None:
        """Forward a cooking status to the coordinator of its appliance."""
        if device := self.devices.get(appliance.thing_uuid):
            device.async_on_status(status)

    @callback
    def _async_on_shadow(self, appliance: Appliance, shadow: CookeoShadow) -> None:
        """Forward a shadow update to the coordinator of its appliance."""
        if device := self.devices.get(appliance.thing_uuid):
            device.async_on_shadow(shadow)


class CookeoCoordinator(DataUpdateCoordinator[CookeoData]):
    """Coordinator that pushes the MQTT updates of one appliance."""

    config_entry: CookeoConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CookeoConfigEntry,
        appliance: Appliance,
        client: IotClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass, LOGGER, config_entry=entry, name=f"{DOMAIN} {appliance.name}"
        )
        self.appliance = appliance
        self._client = client
        self.data = CookeoData()

    @callback
    def async_on_status(self, status: CookeoStatus) -> None:
        """Handle a cooking status update."""
        self.data.status = self.data.status.merge(status)
        self.async_set_updated_data(self.data)

    @callback
    def async_on_shadow(self, shadow: CookeoShadow) -> None:
        """Handle a shadow update."""
        previous = self.data.shadow
        self.data.shadow = previous.merge(shadow)
        if previous.connected is False and self.data.shadow.connected:
            self._client.request_status(self.appliance)
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
