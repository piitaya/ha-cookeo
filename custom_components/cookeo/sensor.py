"""Sensor platform for the Cookeo integration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import CookeoConfigEntry, CookeoCoordinator
from .entity import CookeoEntity
from .lib import MODES, PROGRAMS, STATUSES, CookeoStatus

PARALLEL_UPDATES = 0

OFF = "off"
NO_PROGRAM = "none"


def _option(value: str | None, options: tuple[str, ...]) -> str | None:
    """Return the value if it is a known option."""
    return value if value in options else None


def _program(status: CookeoStatus) -> str | None:
    """Return the program, ``none`` when no program is loaded."""
    if (key := status.display_program_key) is None:
        return NO_PROGRAM
    return PROGRAMS.get(key)


def _local_time(value: datetime | None) -> datetime | None:
    """Attach the Home Assistant time zone to a naive Cookeo time."""
    if value is None:
        return None
    return value.replace(tzinfo=dt_util.get_default_time_zone())


@dataclass(frozen=True, kw_only=True)
class CookeoSensorEntityDescription(SensorEntityDescription):
    """Describes a Cookeo sensor entity."""

    value_fn: Callable[[CookeoStatus], StateType | datetime]
    off_value: str | None = None


SENSORS: tuple[CookeoSensorEntityDescription, ...] = (
    CookeoSensorEntityDescription(
        key="state",
        translation_key="state",
        name=None,
        device_class=SensorDeviceClass.ENUM,
        options=[OFF, *STATUSES],
        value_fn=lambda status: _option(status.normalized_status, STATUSES),
        off_value=OFF,
    ),
    CookeoSensorEntityDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=[OFF, *MODES],
        value_fn=lambda status: _option(status.normalized_mode, MODES),
        off_value=OFF,
    ),
    CookeoSensorEntityDescription(
        key="progress",
        translation_key="progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.display_progress,
    ),
    CookeoSensorEntityDescription(
        key="remaining_time",
        translation_key="remaining_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.display_remaining_time,
    ),
    CookeoSensorEntityDescription(
        key="recipe_title",
        translation_key="recipe_title",
        value_fn=lambda status: status.display_recipe_title,
    ),
    CookeoSensorEntityDescription(
        key="recipe_step",
        translation_key="recipe_step",
        value_fn=lambda status: status.display_recipe_step,
    ),
    CookeoSensorEntityDescription(
        key="operation_start",
        translation_key="operation_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda status: _local_time(status.display_operation_start),
    ),
    CookeoSensorEntityDescription(
        key="end_time",
        translation_key="end_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda status: _local_time(status.display_end_time),
    ),
    CookeoSensorEntityDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.display_target_temperature,
    ),
    CookeoSensorEntityDescription(
        key="program",
        translation_key="program",
        device_class=SensorDeviceClass.ENUM,
        options=[*sorted(set(PROGRAMS.values())), NO_PROGRAM],
        value_fn=_program,
        off_value=NO_PROGRAM,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookeoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Cookeo sensor platform."""
    coordinator = entry.runtime_data
    async_add_entities(
        CookeoSensor(coordinator, description) for description in SENSORS
    )


class CookeoSensor(CookeoEntity, SensorEntity):
    """Representation of a Cookeo sensor."""

    entity_description: CookeoSensorEntityDescription

    def __init__(
        self,
        coordinator: CookeoCoordinator,
        description: CookeoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> StateType | datetime:
        """Return the sensor value, reset once the Cookeo is turned off."""
        data = self.coordinator.data
        if data.shadow.connected is False:
            return self.entity_description.off_value
        return self.entity_description.value_fn(data.status)
