"""Event platform for the Cookeo integration."""

from typing import override

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CookeoConfigEntry, CookeoCoordinator
from .entity import CookeoEntity
from .lib import EVENT_TYPES, CookeoNotification, notification_type

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
        """Initialize the event."""
        super().__init__(coordinator, "notification")
        self._attr_event_types = list(EVENT_TYPES)
        self._last: tuple[CookeoNotification, int | None] | None = None

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Fire the event when a new notification arrives."""
        data = self.coordinator.data
        if (notification := data.notification) and (
            last := (notification, data.status.version)
        ) != self._last:
            self._last = last
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
