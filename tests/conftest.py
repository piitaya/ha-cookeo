"""Fixtures for the Cookeo tests."""

import base64
from collections.abc import Generator
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.cookeo.lib import Appliance, AwsCredentials, Tokens

USER_ID = "005000000000001"

COOKEO = Appliance(
    name="Cookeo Touch", thing_type="COOKEO_TOUCH", thing_uuid="uuid-cookeo", cmmf="1"
)
COMPANION = Appliance(
    name="i-Companion",
    thing_type="COMPANION",
    thing_uuid="uuid-companion",
    cmmf="2",
    domain="PRO_COM",
)


def id_token(user_id: str = USER_ID) -> str:
    """Return an unsigned id token for an account."""
    claims = {"sub": f"https://account.groupeseb.com/id/00D000000000001/{user_id}"}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"header.{payload.decode()}.signature"


def tokens(user_id: str = USER_ID) -> Tokens:
    """Return the tokens of an account."""
    return Tokens(
        access_token="access", id_token=id_token(user_id), refresh_token="refresh"
    )


def profile(*appliances: Appliance) -> dict[str, Any]:
    """Return an SEB profile with the given appliances."""
    return {
        "profilePerimeter": "MOULINEX_FR",
        "household": {
            "products": [
                {
                    "name": appliance.name,
                    "cmmf": appliance.cmmf,
                    "domains": [appliance.domain],
                    "iot": {
                        "thingType": appliance.thing_type,
                        "functionalId": appliance.thing_uuid,
                    },
                }
                for appliance in appliances
            ]
        },
    }


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable the custom integration in every test."""


@pytest.fixture
def mock_cloud() -> Generator[MagicMock]:
    """Mock the SEB cloud, Cognito and the MQTT client."""
    cloud = MagicMock()
    with (
        patch(
            "custom_components.cookeo.lib.oauth.login",
            AsyncMock(return_value=tokens()),
        ) as cloud.login,
        patch(
            "custom_components.cookeo.lib.oauth.refresh",
            AsyncMock(return_value=tokens()),
        ) as cloud.refresh,
        patch(
            "custom_components.cookeo.lib.seb_api.fetch_profile",
            AsyncMock(return_value=profile(COOKEO, COMPANION)),
        ) as cloud.fetch_profile,
        patch(
            "custom_components.cookeo.lib.seb_api.attach_policies", AsyncMock()
        ) as cloud.attach_policies,
        patch(
            "custom_components.cookeo.lib.cognito.federate",
            side_effect=lambda _: AwsCredentials(
                "key", "secret", "session", int(time.time()) + 3600
            ),
        ),
        patch("custom_components.cookeo.coordinator.IotClient") as iot_client,
    ):
        cloud.iot_client = iot_client
        yield cloud
