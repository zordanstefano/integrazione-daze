"""Binary sensor per la wallbox Daze."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DazeConfigEntry
from .api import DazeSocketState
from .const import (
    EVSE_STATE_CHARGING,
    EVSE_STATE_ERROR,
    EVSE_STATES_VEHICLE_CONNECTED,
)
from .entity import DazeEntity


@dataclass(frozen=True, kw_only=True)
class DazeBinarySensorDescription(BinarySensorEntityDescription):
    """Descrizione di un binary sensor Daze."""

    value_fn: Callable[[DazeSocketState], bool]


BINARY_SENSORS: tuple[DazeBinarySensorDescription, ...] = (
    DazeBinarySensorDescription(
        key="vehicle_connected",
        translation_key="vehicle_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda s: s.evse_state in EVSE_STATES_VEHICLE_CONNECTED,
    ),
    DazeBinarySensorDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda s: s.evse_state == EVSE_STATE_CHARGING,
    ),
    DazeBinarySensorDescription(
        key="problem",
        translation_key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: s.evse_state == EVSE_STATE_ERROR
        or bool(s.system_error),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DazeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crea i binary sensor della wallbox."""
    runtime = entry.runtime_data
    async_add_entities(DazeBinarySensor(runtime, desc) for desc in BINARY_SENSORS)


class DazeBinarySensor(DazeEntity, BinarySensorEntity):
    """Binary sensor generico basato su una descrizione."""

    entity_description: DazeBinarySensorDescription

    def __init__(self, runtime, description: DazeBinarySensorDescription) -> None:
        """Inizializza il binary sensor."""
        super().__init__(runtime, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Stato corrente."""
        if self._state is None:
            return None
        return self.entity_description.value_fn(self._state)
