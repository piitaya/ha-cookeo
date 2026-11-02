"""Base entity for the Cookeo integration."""

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import CookeoConfigEntry, CookeoCoordinator


@callback
def async_add_device_entities(
    entry: CookeoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    entities_fn: Callable[[CookeoCoordinator], Iterable[Entity]],
) -> None:
    """Add the entities of each appliance, including the ones linked later."""
    account = entry.runtime_data
    known: set[str] = set()

    @callback
    def _async_add_new_devices() -> None:
        known.intersection_update(account.devices)
        new = account.devices.keys() - known
        known.update(new)
        async_add_entities(
            entity for uuid in new for entity in entities_fn(account.devices[uuid])
        )

    _async_add_new_devices()
    entry.async_on_unload(account.async_add_listener(_async_add_new_devices))


class CookeoEntity(CoordinatorEntity[CookeoCoordinator]):
    """Base class for Cookeo entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CookeoCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        appliance = coordinator.appliance
        self._attr_unique_id = f"{appliance.thing_uuid}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, appliance.thing_uuid)},
            manufacturer=MANUFACTURER,
            model=appliance.name,
            model_id=appliance.cmmf,
            name=appliance.name,
            sw_version=coordinator.data.shadow.firmware.ui_version,
        )
