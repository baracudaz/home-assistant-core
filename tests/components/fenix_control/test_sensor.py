"""Test the FENIX Control sensor platform."""

import dataclasses
from unittest.mock import MagicMock

from pyfenixcontrol import (
    EnergyPeriod,
    FenixControlAuthError,
    FenixControlResponseError,
)
import pytest
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.fenix_control.const import DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import DEVICE, INSTALLATION, INSTALLATION_ID, ROOM, ROOM_ID

ENERGY_ENTITY_ID = "sensor.home_living_room_energy_consumption_today"


@pytest.fixture
def platforms() -> list[Platform]:
    """Load only the sensor platform."""
    return [Platform.SENSOR]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the sensor entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_daily_energy_request(mock_fenix_client: MagicMock) -> None:
    """Test today's energy is requested per hour for the thermostat's room."""
    installation_id, room_id, start, end, period = (
        mock_fenix_client.get_room_energy.call_args.args
    )

    assert (installation_id, room_id, period) == (
        INSTALLATION_ID,
        ROOM_ID,
        EnergyPeriod.HOUR,
    )
    assert start < end


@pytest.mark.parametrize(
    ("buckets", "error", "state"),
    [
        pytest.param([], None, "0.0", id="no_buckets"),
        pytest.param(
            None, FenixControlResponseError("HTTP 500", 500), STATE_UNKNOWN, id="error"
        ),
    ],
)
async def test_daily_energy_without_data(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_fenix_client: MagicMock,
    buckets: list[object] | None,
    error: Exception | None,
    state: str,
) -> None:
    """Test today's energy without buckets, and when the request fails."""
    mock_fenix_client.get_room_energy.return_value = buckets
    mock_fenix_client.get_room_energy.side_effect = error
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(ENERGY_ENTITY_ID).state == state


async def test_daily_energy_per_room(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_fenix_client: MagicMock,
) -> None:
    """Test a room with two thermostats is requested once and shown on both."""
    second = dataclasses.replace(DEVICE, id="AA11BB22CC01", name="Living room 2")
    room = dataclasses.replace(ROOM, device_ids=(DEVICE.id, second.id))
    mock_fenix_client.get_installations.return_value = [
        dataclasses.replace(INSTALLATION, rooms=(room,))
    ]
    mock_fenix_client.get_devices.return_value = [DEVICE, second]
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_fenix_client.get_room_energy.assert_awaited_once()
    assert hass.states.get(ENERGY_ENTITY_ID).state == "200.5"
    assert (
        hass.states.get("sensor.home_living_room_2_energy_consumption_today").state
        == "200.5"
    )


async def test_daily_energy_auth_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_fenix_client: MagicMock,
) -> None:
    """Test a rejected energy request starts reauthentication."""
    mock_fenix_client.get_room_energy.side_effect = FenixControlAuthError("expired")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_temperature_sensors_not_reported(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_fenix_client: MagicMock,
) -> None:
    """Test temperature sensors are only created for reported temperatures."""
    mock_fenix_client.get_devices.return_value = [
        dataclasses.replace(DEVICE, room_temperature=None, floor_temperature=None)
    ]
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entries = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    assert sorted(entry.entity_id for entry in entries) == [
        "climate.home_living_room",
        ENERGY_ENTITY_ID,
    ]
