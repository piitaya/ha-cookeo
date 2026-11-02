"""Tests for the setup and migration of the Cookeo integration."""

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cookeo.const import DOMAIN
from custom_components.cookeo.lib import CookeoError, CookeoStatus
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import COMPANION, COOKEO, USER_ID, profile, tokens


def account_entry() -> MockConfigEntry:
    """Return a config entry of the account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="user@example.com",
        unique_id=USER_ID,
        version=2,
        data={
            "tokens": {
                "access_token": "access",
                "id_token": tokens().id_token,
                "refresh_token": "refresh",
            },
            "profile_perimeter": "MOULINEX_FR",
        },
    )


def appliance_entry(appliance_uuid: str, name: str) -> MockConfigEntry:
    """Return a config entry of one appliance, as created before the migration."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=name,
        unique_id=appliance_uuid,
        version=1,
        data={
            "tokens": {
                "access_token": "access",
                "id_token": tokens().id_token,
                "refresh_token": "refresh",
                "issued_at": 0,
                "id_token_exp": 0,
            },
            "profile_perimeter": "MOULINEX_FR",
            "appliance": {
                "name": name,
                "thing_type": "COOKEO_TOUCH",
                "thing_uuid": appliance_uuid,
            },
        },
    )


def device_uuids(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    """Return the appliances of the devices of a config entry."""
    return {
        uuid
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for _, uuid in device.identifiers
    }


async def test_setup_adds_all_appliances(
    hass: HomeAssistant, mock_cloud: MagicMock
) -> None:
    entry = account_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert device_uuids(hass, entry) == {COOKEO.thing_uuid, COMPANION.thing_uuid}
    assert hass.states.get("sensor.cookeo_touch_program") is not None
    assert hass.states.get("sensor.i_companion_program") is not None
    client = mock_cloud.iot_client.return_value
    assert client.reconnect.call_args.args[1] == [COOKEO, COMPANION]
    assert {call.args[2] for call in mock_cloud.attach_policies.call_args_list} == {
        "COOKEO_TOUCH",
        "COMPANION",
    }


async def test_setup_retries_when_cloud_fails(
    hass: HomeAssistant, mock_cloud: MagicMock
) -> None:
    mock_cloud.refresh.side_effect = CookeoError("down")
    entry = account_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_status_updates_the_right_appliance(
    hass: HomeAssistant, mock_cloud: MagicMock
) -> None:
    entry = account_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    on_status = mock_cloud.iot_client.call_args.kwargs["on_status"]
    on_status(COOKEO, CookeoStatus(current_status="cooking"))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.cookeo_touch").state == "cooking"
    assert hass.states.get("sensor.i_companion").state == "unknown"


async def test_appliances_linked_and_removed_later(
    hass: HomeAssistant, mock_cloud: MagicMock, freezer: FrozenDateTimeFactory
) -> None:
    mock_cloud.fetch_profile.return_value = profile(COOKEO)
    entry = account_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert device_uuids(hass, entry) == {COOKEO.thing_uuid}

    mock_cloud.fetch_profile.return_value = profile(COMPANION)
    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert device_uuids(hass, entry) == {COMPANION.thing_uuid}
    assert hass.states.get("sensor.i_companion_program") is not None
    assert hass.states.get("sensor.cookeo_touch_program") is None


async def test_empty_profile_keeps_devices(
    hass: HomeAssistant, mock_cloud: MagicMock, freezer: FrozenDateTimeFactory
) -> None:
    entry = account_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    mock_cloud.fetch_profile.return_value = profile()
    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert device_uuids(hass, entry) == {COOKEO.thing_uuid, COMPANION.thing_uuid}


async def test_migrate_appliance_entry(
    hass: HomeAssistant, mock_cloud: MagicMock
) -> None:
    entry = appliance_entry(COOKEO.thing_uuid, "Cookeo Touch")
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    old_entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{COOKEO.thing_uuid}_program",
        config_entry=entry,
        suggested_object_id="my_cookeo_program",
    )

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.unique_id == USER_ID
    assert set(entry.data) == {"tokens", "profile_perimeter"}
    assert set(entry.data["tokens"]) == {"access_token", "id_token", "refresh_token"}
    assert entity_registry.async_get(old_entity.entity_id).config_entry_id == (
        entry.entry_id
    )
    assert device_uuids(hass, entry) == {COOKEO.thing_uuid, COMPANION.thing_uuid}


async def test_migrate_two_entries_of_the_same_account(
    hass: HomeAssistant, mock_cloud: MagicMock
) -> None:
    first = appliance_entry(COOKEO.thing_uuid, "Cookeo Touch")
    second = appliance_entry(COMPANION.thing_uuid, "i-Companion")
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    companion_entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{COMPANION.thing_uuid}_program",
        config_entry=second,
        suggested_object_id="my_companion_program",
    )

    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert entries == [first]
    assert first.state is ConfigEntryState.LOADED
    assert device_uuids(hass, first) == {COOKEO.thing_uuid, COMPANION.thing_uuid}
    assert hass.states.get(companion_entity.entity_id) is not None
