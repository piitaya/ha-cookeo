"""Client for the SEB cloud of the Cookeo, without Home Assistant dependencies."""

from .const import COOKEO_DOMAIN
from .exceptions import CookeoError, LoginError
from .iot_client import IotClient
from .models import (
    MODES,
    STATUSES,
    Appliance,
    AwsCredentials,
    CookeoFirmware,
    CookeoShadow,
    CookeoStatus,
    Tokens,
)
from .programs import PROGRAMS

__all__ = [
    "COOKEO_DOMAIN",
    "MODES",
    "PROGRAMS",
    "STATUSES",
    "Appliance",
    "AwsCredentials",
    "CookeoError",
    "CookeoFirmware",
    "CookeoShadow",
    "CookeoStatus",
    "IotClient",
    "LoginError",
    "Tokens",
]
