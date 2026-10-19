"""Base entity for the Cookeo integration."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import CookeoCoordinator


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
