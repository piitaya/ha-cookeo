"""Cookeo cloud protocol library.

Pure-Python, no Home Assistant dependency. Talks to the SEB cloud
(Salesforce OAuth → Cognito → AWS IoT MQTT) on behalf of the
``custom_components/cookeo/`` integration that lives next to it.
"""

from .const import (
    AWS_REGION,
    COGNITO_IDENTITY_POOL,
    COGNITO_PROVIDER,
    COOKEO_API_KEY,
    MQTT_ENDPOINT,
    SEB_API_BASE,
    SEB_DOMAIN,
    SEB_IOT_BASE,
    SF_BASE,
    SF_CLIENT_ID,
    SF_REDIRECT_URI,
    USER_AGENT,
)
from .iot_client import IotClient
from .models import (
    ApplianceInfo,
    AwsCredentials,
    CookeoFirmware,
    CookeoNotification,
    CookeoProgram,
    CookeoShadow,
    CookeoStatus,
    Tokens,
)

__all__ = [
    "AWS_REGION",
    "ApplianceInfo",
    "AwsCredentials",
    "COGNITO_IDENTITY_POOL",
    "COGNITO_PROVIDER",
    "COOKEO_API_KEY",
    "CookeoFirmware",
    "CookeoNotification",
    "CookeoProgram",
    "CookeoShadow",
    "CookeoStatus",
    "IotClient",
    "KNOWN_MODES",
    "KNOWN_STATUSES",
    "MQTT_ENDPOINT",
    "SEB_API_BASE",
    "SEB_DOMAIN",
    "SEB_IOT_BASE",
    "SF_BASE",
    "SF_CLIENT_ID",
    "SF_REDIRECT_URI",
    "Tokens",
    "USER_AGENT",
]

#: Canonical ``currentStatus`` values. Used as the HA enum sensor's
#: ``options`` list. ``depressurization`` (with z) is the official
#: spelling even though "depressurising" reads better — we keep the
#: wire value.
#:
#: ``off`` is an integration-side value surfaced when the device's
#: shadow reports ``connected: false`` so we don't display a stale
#: last-known state from before the device went offline.
KNOWN_STATUSES: tuple[str, ...] = (
    "off",
    "stopped",
    "ready",
    "paused",
    "warming",
    "cooking",
    "depressurization",
    "keep_warm",
    "done",
    "delayed",
    "alert",
)

#: Canonical ``mode`` values. The wire format uses camelCase
#: (``"ingredientCooking"``); we snake_case everywhere we expose it
#: for consistency with state values.
#:
#: ``off`` is integration-side, surfaced when the device's shadow
#: reports ``connected: false`` — same pattern as the state sensor.
KNOWN_MODES: tuple[str, ...] = (
    "off",
    "manual",
    "program",
    "recipe",
    "ingredient_cooking",
)


def normalize_mode(raw: str | None) -> str | None:
    """``"ingredientCooking"`` → ``"ingredient_cooking"`` (and friends)."""
    if raw is None:
        return None
    # Insert underscore before each interior uppercase letter, then lowercase.
    out: list[str] = []
    for i, ch in enumerate(raw.strip()):
        if i and ch.isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def normalize_status(raw: str | None) -> str | None:
    """Map an upstream ``currentStatus`` string to our canonical snake_case.

    The appliance reports values like ``"keep warm"`` with spaces and
    inconsistent casing. We snake_case them and pass everything else
    through; downstream entities can decide whether to display the raw
    value when not in :data:`KNOWN_STATUSES`.
    """
    if raw is None:
        return None
    return raw.strip().lower().replace(" ", "_").replace("-", "_")
