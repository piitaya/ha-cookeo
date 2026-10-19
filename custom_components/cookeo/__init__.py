"""The Cookeo integration."""

import aiohttp

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import CookeoConfigEntry, CookeoCoordinator
from .lib import CookeoError

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: CookeoConfigEntry) -> bool:
    """Set up Cookeo from a config entry."""
    coordinator = CookeoCoordinator(hass, entry)
    try:
        await coordinator.async_connect()
    except (CookeoError, aiohttp.ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="connection_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CookeoConfigEntry) -> bool:
    """Unload a Cookeo config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
