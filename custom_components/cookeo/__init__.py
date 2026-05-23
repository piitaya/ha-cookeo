"""The Cookeo integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_APPLIANCE, CONF_TOKENS, DOMAIN
from .coordinator import CookeoCoordinator
from .lib import ApplianceInfo, Tokens

type CookeoEntry = ConfigEntry[CookeoCoordinator]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: CookeoEntry) -> bool:
    """Set up a Cookeo appliance from a config entry."""
    tokens_data = entry.data[CONF_TOKENS]
    appliance_data = entry.data[CONF_APPLIANCE]

    tokens = Tokens(
        access_token=tokens_data["access_token"],
        id_token=tokens_data["id_token"],
        refresh_token=tokens_data.get("refresh_token"),
        issued_at=tokens_data.get("issued_at", 0),
        id_token_exp=tokens_data.get("id_token_exp", 0),
    )
    appliance = ApplianceInfo(
        appliance_id=appliance_data["appliance_id"],
        name=appliance_data["name"],
        thing_type=appliance_data["thing_type"],
        thing_uuid=appliance_data["thing_uuid"],
        functional_id=appliance_data["functional_id"],
        rcu_asset_id=appliance_data.get("rcu_asset_id", ""),
        cmmf=appliance_data.get("cmmf"),
    )

    coordinator = CookeoCoordinator(hass, entry, appliance, tokens)
    try:
        await coordinator.async_setup()
    except Exception as err:  # noqa: BLE001
        raise ConfigEntryNotReady(
            f"Could not connect to Cookeo {appliance.name}: {err}"
        ) from err

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CookeoEntry) -> bool:
    """Tear down the integration cleanly."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: CookeoCoordinator = entry.runtime_data
        await coordinator.async_shutdown()
    return unloaded
