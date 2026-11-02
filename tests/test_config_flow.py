"""Tests for the Cookeo config flow."""

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cookeo.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import USER_ID, profile, tokens

USER_INPUT = {
    "region": "MOULINEX_FR",
    "email": "user@example.com",
    "password": "secret",
}


async def test_create_account_entry(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert result["result"].unique_id == USER_ID
    assert result["data"] == {
        "tokens": {
            "access_token": "access",
            "id_token": tokens().id_token,
            "refresh_token": "refresh",
        },
        "profile_perimeter": "MOULINEX_FR",
    }


async def test_no_appliances(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    mock_cloud.fetch_profile.return_value = profile()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_appliances"


async def test_already_configured(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=USER_ID, version=2).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (TimeoutError, "cannot_connect"),
        (ValueError, "unknown"),
    ],
)
async def test_errors(
    hass: HomeAssistant,
    mock_cloud: MagicMock,
    side_effect: type[Exception],
    error: str,
) -> None:
    mock_cloud.login.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_cloud.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
