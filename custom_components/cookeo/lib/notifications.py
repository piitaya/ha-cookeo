"""Notification codes of the Cookeo."""

UNKNOWN_NOTIFICATION = "unknown"

NOTIFICATION_TYPES: dict[str, str] = {}

EVENT_TYPES = (*sorted(set(NOTIFICATION_TYPES.values())), UNKNOWN_NOTIFICATION)


def notification_type(code: str) -> str:
    """Return the event type of a notification code."""
    return NOTIFICATION_TYPES.get(code, UNKNOWN_NOTIFICATION)
