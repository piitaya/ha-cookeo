"""Base entity for Cookeo."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CookeoCoordinator


class CookeoEntity(CoordinatorEntity[CookeoCoordinator]):
    """Base entity tied to a single Cookeo appliance."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CookeoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        appliance = coordinator.appliance
        self._attr_unique_id = f"{appliance.thing_uuid}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, appliance.thing_uuid)},
            name=appliance.name or f"Cookeo {appliance.thing_uuid[:8]}",
            manufacturer="Moulinex",
            model=appliance.name or "Cookeo",
            model_id=appliance.cmmf or None,
            sw_version=self.coordinator.data.shadow.firmware.ui_version,
        )
