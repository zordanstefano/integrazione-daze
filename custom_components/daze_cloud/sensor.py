"""Sensori per la wallbox Daze."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DazeConfigEntry
from .api import DazeSocketState
from .const import EVSE_STATE_NAMES
from .entity import DazeEntity


def _scale(value: float | None, factor: float) -> float | None:
    """Riscala un valore tollerando l'assenza del dato."""
    return None if value is None else value / factor


# Al collegamento del cavo la wallbox registra una sessione di pochi secondi
# (l'handshake), poi congelata finche' la carica vera non parte. Non e' tempo di
# ricarica: la durata inizia a contare solo oltre questa soglia.
_HANDSHAKE_SECONDS: Final = 15


def _format_duration(seconds: float | None) -> str | None:
    """Formatta una durata in ore e minuti, ignorando i secondi.

    I secondi non aggiungono nulla su una ricarica e rendono il valore
    illeggibile a colpo d'occhio: "1 h 23 min" invece di "4980".
    """
    if seconds is None:
        return None
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def _session_duration(seconds: float | None) -> str | None:
    """Durata della sessione, scartando l'handshake iniziale (<= 15 s)."""
    if seconds is not None and seconds <= _HANDSHAKE_SECONDS:
        seconds = 0
    return _format_duration(seconds)


@dataclass(frozen=True, kw_only=True)
class DazeSensorDescription(SensorEntityDescription):
    """Descrizione di un sensore Daze con la funzione di lettura."""

    value_fn: Callable[[DazeSocketState], float | str | None]


SENSORS: tuple[DazeSensorDescription, ...] = (
    DazeSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=3,
        value_fn=lambda s: _scale(s.power_w, 1000),
    ),
    DazeSensorDescription(
        key="energy_session",
        translation_key="energy_session",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda s: _scale(s.energy_wh, 1000),
    ),
    # Energia erogata a vita dalla wallbox: contatore cumulato, adatto alla
    # dashboard Energia di HA. L'API non lo espone direttamente, lo ricava
    # aggregando lo storico (vedi api.py).
    DazeSensorDescription(
        key="total_energy",
        translation_key="total_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda s: _scale(s.total_energy_wh, 1000),
    ),
    # Corrente e tensione di carica esistono solo durante una sessione: a riposo
    # restano vuote (in HA "Sconosciuto"). Restituire un testo tipo "Non in carica"
    # sarebbe piu' esplicito ma impedirebbe unita' di misura e statistiche.
    DazeSensorDescription(
        key="current_l1",
        translation_key="current_l1",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        value_fn=lambda s: s.current_l1,
    ),
    DazeSensorDescription(
        key="current_l2",
        translation_key="current_l2",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.current_l2,
    ),
    DazeSensorDescription(
        key="current_l3",
        translation_key="current_l3",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.current_l3,
    ),
    DazeSensorDescription(
        key="voltage_l1",
        translation_key="voltage_l1",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=0,
        value_fn=lambda s: s.voltage_l1,
    ),
    DazeSensorDescription(
        key="voltage_l2",
        translation_key="voltage_l2",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.voltage_l2,
    ),
    DazeSensorDescription(
        key="voltage_l3",
        translation_key="voltage_l3",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.voltage_l3,
    ),
    # Testo "1 h 23 min": niente device_class ne' unita', che ammettono solo numeri.
    DazeSensorDescription(
        key="charge_time",
        translation_key="charge_time",
        value_fn=lambda s: _session_duration(s.charge_seconds),
    ),
    DazeSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(EVSE_STATE_NAMES.values())),
        value_fn=lambda s: EVSE_STATE_NAMES.get(s.evse_state, "unknown"),
    ),
    # Corrente prelevata dalla rete: base per la futura ricarica su surplus FV.
    DazeSensorDescription(
        key="grid_current_l1",
        translation_key="grid_current_l1",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_fn=lambda s: s.grid_current_l1,
    ),
    DazeSensorDescription(
        key="grid_current_l2",
        translation_key="grid_current_l2",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.grid_current_l2,
    ),
    DazeSensorDescription(
        key="grid_current_l3",
        translation_key="grid_current_l3",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.grid_current_l3,
    ),
    # Non una misura ma l'impostazione della wallbox, in sola lettura: si cambia
    # dal controllo in potenza (`number.max_power` / `select.max_power`), che
    # rispetta la scala di step ammessa dall'app Daze.
    DazeSensorDescription(
        key="max_current",
        translation_key="max_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_fn=lambda s: s.max_current_a,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DazeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crea i sensori della wallbox."""
    runtime = entry.runtime_data
    async_add_entities(DazeSensor(runtime, desc) for desc in SENSORS)


class DazeSensor(DazeEntity, SensorEntity):
    """Sensore generico basato su una descrizione."""

    entity_description: DazeSensorDescription

    def __init__(self, runtime, description: DazeSensorDescription) -> None:
        """Inizializza il sensore."""
        super().__init__(runtime, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        """Valore corrente del sensore."""
        if self._state is None:
            return None
        return self.entity_description.value_fn(self._state)
