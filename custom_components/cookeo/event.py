"""Cookeo event entities."""

from __future__ import annotations

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CookeoEntry
from .coordinator import CookeoCoordinator
from .entity import CookeoEntity
from .lib.notifications import NOTIFICATION_KEY_VALUES, code_to_key

NOTIFICATION_DESCRIPTION = EventEntityDescription(
    key="notification",
    translation_key="notification",
    event_types=list(NOTIFICATION_KEY_VALUES),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookeoEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([CookeoNotificationEvent(coordinator)])


class CookeoNotificationEvent(CookeoEntity, EventEntity):
    entity_description = NOTIFICATION_DESCRIPTION

    def __init__(self, coordinator: CookeoCoordinator) -> None:
        super().__init__(coordinator, "notification")
        self.entity_description = NOTIFICATION_DESCRIPTION
        self._last_seen_version: int | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Snapshot the current version so we only fire on future
        # notifications, not on a stale one from before HA was listening.
        self._last_seen_version = self.coordinator.data.status.version

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        status = self.coordinator.data.status
        notif = status.notification
        if notif is None:
            return
        if status.version == self._last_seen_version:
            return
        self._last_seen_version = status.version

        self._trigger_event(
            code_to_key(notif.code),
            {
                "code": notif.code,
                "severity": notif.severity,
                "time_to_live": notif.time_to_live,
                "title": notif.title,
                "description": notif.description,
                "title_key": notif.title_key,
            },
        )
        self.async_write_ha_state()
