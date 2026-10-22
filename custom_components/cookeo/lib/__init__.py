"""Client for the SEB cloud of the Cookeo, without Home Assistant dependencies."""

from .exceptions import CookeoError, LoginError
from .iot_client import IotClient
from .models import (
    MODES,
    STATUSES,
    Appliance,
    AwsCredentials,
    CookeoFirmware,
    CookeoNotification,
    CookeoShadow,
    CookeoStatus,
    Tokens,
)
from .notifications import EVENT_TYPES, notification_type
from .programs import PROGRAMS

__all__ = [
    "EVENT_TYPES",
    "MODES",
    "PROGRAMS",
    "STATUSES",
    "Appliance",
    "AwsCredentials",
    "CookeoError",
    "CookeoFirmware",
    "CookeoNotification",
    "CookeoShadow",
    "CookeoStatus",
    "IotClient",
    "LoginError",
    "Tokens",
    "notification_type",
]
