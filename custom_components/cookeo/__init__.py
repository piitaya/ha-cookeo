"""The Cookeo integration."""

from dataclasses import asdict

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_PROFILE_PERIMETER, CONF_TOKENS, DOMAIN, LOGGER
from .coordinator import CookeoAccountCoordinator, CookeoConfigEntry, tokens_from_data

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: CookeoConfigEntry) -> bool:
    """Set up Cookeo from a config entry."""
    coordinator = CookeoAccountCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CookeoConfigEntry) -> bool:
    """Unload a Cookeo config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: CookeoConfigEntry) -> bool:
    """Turn a config entry of one appliance into a config entry of the account."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        tokens = tokens_from_data(entry.data[CONF_TOKENS])
        if hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, tokens.user_id):
            LOGGER.info(
                "Removing %s, its account is already set up by another entry",
                entry.title,
            )
            hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
            return False
        hass.config_entries.async_update_entry(
            entry,
            data={
                CONF_TOKENS: asdict(tokens),
                CONF_PROFILE_PERIMETER: entry.data[CONF_PROFILE_PERIMETER],
            },
            unique_id=tokens.user_id,
            version=2,
        )
    return True
