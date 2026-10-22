"""Event platform for the Cookeo integration."""

from typing import override

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CookeoConfigEntry, CookeoCoordinator
from .entity import CookeoEntity
from .lib import EVENT_TYPES, notification_type

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookeoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Cookeo event platform."""
    async_add_entities([CookeoNotificationEvent(entry.runtime_data)])


class CookeoNotificationEvent(CookeoEntity, EventEntity):
    """Event fired for each notification of a Cookeo."""

    _attr_translation_key = "notification"

    def __init__(self, coordinator: CookeoCoordinator) -> None:
        """Initialize the event, ignoring the notification already received."""
        super().__init__(coordinator, "notification")
        self._attr_event_types = list(EVENT_TYPES)
        self._version = coordinator.data.status.version

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Fire the event when a new notification arrives."""
        status = self.coordinator.data.status
        if (notification := status.notification) and status.version != self._version:
            self._version = status.version
            self._trigger_event(
                notification_type(notification.code),
                {
                    "code": notification.code,
                    "severity": notification.severity,
                    "time_to_live": notification.time_to_live,
                    "title": notification.title,
                    "description": notification.description,
                    "title_key": notification.title_key,
                },
            )
        super()._handle_coordinator_update()
