"""Sensor platform for the FENIX Control integration."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from pyfenixcontrol import Device

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    FenixControlConfigEntry,
    FenixControlCoordinator,
    FenixControlData,
)
from .entity import FenixControlEntity

# Read-only platform; the coordinator fetches the data
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FenixControlSensorEntityDescription(SensorEntityDescription):
    """Describes a FENIX Control sensor."""

    value_fn: Callable[[Device, FenixControlData], float | None]
    exists_fn: Callable[[Device], bool] = lambda _: True


SENSORS: tuple[FenixControlSensorEntityDescription, ...] = (
    FenixControlSensorEntityDescription(
        key="daily_energy_consumption",
        translation_key="daily_energy_consumption",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_display_precision=0,
        value_fn=lambda device, data: data.daily_energy.get(device.id),
    ),
    FenixControlSensorEntityDescription(
        key="room_temperature",
        translation_key="room_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda device, _: device.room_temperature,
        exists_fn=lambda device: device.room_temperature is not None,
    ),
    FenixControlSensorEntityDescription(
        key="floor_temperature",
        translation_key="floor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=lambda device, _: device.floor_temperature,
        exists_fn=lambda device: device.floor_temperature is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FenixControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FENIX Control sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        FenixControlSensor(coordinator, device_id, description)
        for device_id, device in coordinator.data.devices.items()
        for description in SENSORS
        if description.exists_fn(device)
    )


class FenixControlSensor(FenixControlEntity, SensorEntity):
    """A value measured by a FENIX thermostat."""

    entity_description: FenixControlSensorEntityDescription

    def __init__(
        self,
        coordinator: FenixControlCoordinator,
        device_id: str,
        description: FenixControlSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    @override
    def native_value(self) -> float | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.device, self.coordinator.data)
