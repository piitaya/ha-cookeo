"""Binary sensor platform for the Cookeo integration."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CookeoConfigEntry, CookeoCoordinator
from .entity import CookeoEntity
from .lib import CookeoShadow

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class CookeoBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Cookeo binary sensor entity."""

    is_on_fn: Callable[[CookeoShadow], bool | None]


BINARY_SENSORS: tuple[CookeoBinarySensorEntityDescription, ...] = (
    CookeoBinarySensorEntityDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda shadow: shadow.connected,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookeoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Cookeo binary sensor platform."""
    coordinator = entry.runtime_data
    async_add_entities(
        CookeoBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class CookeoBinarySensor(CookeoEntity, BinarySensorEntity):
    """Representation of a Cookeo binary sensor."""

    entity_description: CookeoBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: CookeoCoordinator,
        description: CookeoBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the binary sensor state."""
        return self.entity_description.is_on_fn(self.coordinator.data.shadow)
