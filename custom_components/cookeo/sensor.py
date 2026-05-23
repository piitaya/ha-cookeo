"""Cookeo sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CookeoEntry
from .coordinator import CookeoData
from .entity import CookeoEntity
from .lib import KNOWN_MODES, KNOWN_STATUSES, normalize_mode
from .lib.programs import PROGRAM_OPTIONS, program_to_key


def _localize(dt: datetime | None) -> datetime | None:
    # Cookeo firmware tags timestamps with a fixed ``+01`` offset (no
    # DST awareness). We treat the wall-clock as the user's local time
    # and re-attach HA's timezone so the frontend renders correctly
    # across DST changes.
    if dt is None:
        return None
    return dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


@dataclass(frozen=True, kw_only=True)
class CookeoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[CookeoData], int | str | datetime | None]


NO_PROGRAM = "none"
NO_RECIPE = "Aucune"


def _with_offline_default(default, getter):
    # Gated ``display_*`` properties keep returning their last cached
    # value when the shadow flips ``connected: false`` (status fields
    # stay in place). Force a reset so e.g. the recipe sensor doesn't
    # keep showing the previous title after the Cookeo powers off.
    def inner(d: CookeoData):
        if d.shadow.connected is False:
            return default
        return getter(d)
    return inner


SENSORS: tuple[CookeoSensorDescription, ...] = (
    CookeoSensorDescription(
        key="state",
        translation_key="state",
        name=None,
        device_class=SensorDeviceClass.ENUM,
        options=list(KNOWN_STATUSES) + ["unknown"],
        value_fn=lambda d: (
            "off" if d.shadow.connected is False
            else (d.status.normalized_status or "unknown")
        ),
    ),
    CookeoSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(KNOWN_MODES) + ["unknown"],
        value_fn=lambda d: (
            "off" if d.shadow.connected is False
            else (normalize_mode(d.status.mode) or "unknown")
        ),
    ),
    CookeoSensorDescription(
        key="progress",
        translation_key="progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_with_offline_default(None, lambda d: d.status.display_progress),
    ),
    CookeoSensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_with_offline_default(None, lambda d: d.status.display_remaining_time),
    ),
    CookeoSensorDescription(
        key="recipe_title",
        translation_key="recipe_title",
        value_fn=_with_offline_default(
            NO_RECIPE, lambda d: d.status.display_recipe_title or NO_RECIPE
        ),
    ),
    CookeoSensorDescription(
        key="recipe_step",
        translation_key="recipe_step",
        # No state_class: this returns numbers OR the "Aucune" sentinel,
        # and MEASUREMENT would feed garbage into HA's statistics.
        value_fn=_with_offline_default(
            NO_RECIPE,
            lambda d: (
                str(d.status.display_recipe_step)
                if d.status.display_recipe_step is not None
                else NO_RECIPE
            ),
        ),
    ),
    CookeoSensorDescription(
        key="operation_start",
        translation_key="operation_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_with_offline_default(
            None, lambda d: _localize(d.status.operation_start_dt)
        ),
    ),
    CookeoSensorDescription(
        key="end_time",
        translation_key="end_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_with_offline_default(
            None, lambda d: _localize(d.status.display_end_time_dt)
        ),
    ),
    CookeoSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_with_offline_default(
            None, lambda d: d.status.display_target_temperature
        ),
    ),
    CookeoSensorDescription(
        key="program",
        translation_key="program",
        device_class=SensorDeviceClass.ENUM,
        options=list(PROGRAM_OPTIONS) + [NO_PROGRAM],
        value_fn=_with_offline_default(
            NO_PROGRAM,
            lambda d: program_to_key(d.status.display_program_id) or NO_PROGRAM,
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookeoEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(CookeoSensor(coordinator, d) for d in SENSORS)


class CookeoSensor(CookeoEntity, SensorEntity):
    entity_description: CookeoSensorDescription

    def __init__(self, coordinator, description: CookeoSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | str | datetime | None:
        return self.entity_description.value_fn(self.coordinator.data)
