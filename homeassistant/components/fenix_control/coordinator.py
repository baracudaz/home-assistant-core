"""Data update coordinator for the FENIX Control integration."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import override

from pyfenixcontrol import (
    Device,
    EnergyPeriod,
    FenixControlAuthError,
    FenixControlClient,
    FenixControlError,
    Installation,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER, SCAN_INTERVAL

# The cloud serves energy per room; limit the parallel requests per update
MAX_PARALLEL_ENERGY_REQUESTS = 5

type FenixControlConfigEntry = ConfigEntry[FenixControlCoordinator]


@dataclass
class FenixControlData:
    """State of all thermostats of the account."""

    devices: dict[str, Device]
    # Today's energy consumption per thermostat in Wh, None if unavailable
    daily_energy: dict[str, float | None]


class FenixControlCoordinator(DataUpdateCoordinator[FenixControlData]):
    """Poll the state of all thermostats of the account."""

    config_entry: FenixControlConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: FenixControlConfigEntry,
        client: FenixControlClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client

    @override
    async def _async_update_data(self) -> FenixControlData:
        """Fetch the state of all thermostats."""
        try:
            installations = await self.client.get_installations()
            for installation in installations:
                await self._trigger_installation_update(installation.id)
            daily_energy = await self._async_get_daily_energy(installations)
            # Read the devices last so the snapshot is as fresh as possible
            devices = await self.client.get_devices(installations)
        except FenixControlAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except FenixControlError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="update_failed"
            ) from err

        return FenixControlData(
            devices={device.id: device for device in devices},
            daily_energy=daily_energy,
        )

    async def _trigger_installation_update(self, installation_id: str) -> None:
        """Ask the thermostats of an installation to report fresh values."""
        try:
            await self.client.trigger_installation_update(installation_id)
        except FenixControlAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except FenixControlError as err:
            # Best effort: the cloud often rejects this with a transient 5xx
            LOGGER.debug(
                "Update trigger for installation %s failed: %s", installation_id, err
            )

    async def _async_get_daily_energy(
        self, installations: list[Installation]
    ) -> dict[str, float | None]:
        """Return today's energy consumption per thermostat in Wh."""
        # The cloud reports energy per room; each thermostat shows its room's total
        rooms = [
            (installation.id, room)
            for installation in installations
            for room in installation.rooms
        ]
        semaphore = asyncio.Semaphore(MAX_PARALLEL_ENERGY_REQUESTS)
        energy = await asyncio.gather(
            *(
                self._async_get_room_energy(semaphore, installation_id, room.id)
                for installation_id, room in rooms
            )
        )
        return {
            device_id: value
            for (_, room), value in zip(rooms, energy, strict=True)
            for device_id in room.device_ids
        }

    async def _async_get_room_energy(
        self, semaphore: asyncio.Semaphore, installation_id: str, room_id: str
    ) -> float | None:
        """Return today's energy consumption of a room in Wh."""
        start = dt_util.start_of_local_day()
        end = min(start + timedelta(days=1), dt_util.now())
        try:
            async with semaphore:
                metrics = await self.client.get_room_energy(
                    installation_id, room_id, start, end, EnergyPeriod.HOUR
                )
        except FenixControlAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except FenixControlError as err:
            LOGGER.debug("Energy request for room %s failed: %s", room_id, err)
            return None
        return sum((metric.processed_value for metric in metrics), 0.0)
