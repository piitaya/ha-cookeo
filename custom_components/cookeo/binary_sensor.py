"""Cookeo binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CookeoEntry
from .coordinator import CookeoData
from .entity import CookeoEntity


@dataclass(frozen=True, kw_only=True)
class CookeoBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[CookeoData], bool | None]


BINARY_SENSORS: tuple[CookeoBinarySensorDescription, ...] = (
    CookeoBinarySensorDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.shadow.connected,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookeoEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(CookeoBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class CookeoBinarySensor(CookeoEntity, BinarySensorEntity):
    entity_description: CookeoBinarySensorDescription

    def __init__(
        self, coordinator, description: CookeoBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)
