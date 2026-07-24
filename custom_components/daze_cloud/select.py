"""Select con la potenza massima di carica, sugli step esatti dell'app Daze."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DazeConfigEntry, DazeRuntimeData
from .api import allowed_steps
from .const import (
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
)
from .entity import DazeChargingLimitEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DazeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crea la tendina della potenza massima di carica."""
    options = entry.options
    steps = allowed_steps(
        options.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT),
        options.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT),
    )
    async_add_entities([DazeMaxPowerSelect(entry.runtime_data, steps)])


class DazeMaxPowerSelect(DazeChargingLimitEntity, SelectEntity):
    """Potenza massima di carica scelta da un elenco, senza valori intermedi.

    E' la stessa impostazione di `number.*_potenza_massima_di_carica` - stesso
    nome, dominio diverso - ma esposta come tendina: il cursore del number puo'
    solo agganciarsi allo step piu' vicino, perche' Home Assistant non sa
    rappresentare una scala discreta non uniforme, mentre qui si sceglie
    direttamente il valore voluto.
    """

    entity_description = SelectEntityDescription(
        key="max_power",
        translation_key="max_power",
    )

    def __init__(
        self, runtime: DazeRuntimeData, steps: tuple[tuple[float, int], ...]
    ) -> None:
        """Inizializza la tendina sulla scala di step ammessa."""
        super().__init__(runtime, "max_power", steps)

    def _label(self, index: int) -> str:
        """Etichetta dello step, nel formato dell'app: "6,1 kW"."""
        return f"{self._kw(index):.1f}".replace(".", ",") + " kW"

    @property
    def options(self) -> list[str]:
        """Gli step selezionabili, dal piu' basso al piu' alto."""
        return [self._label(i) for i in range(len(self._steps))]

    @property
    def current_option(self) -> str | None:
        """Step attualmente impostato sulla wallbox."""
        milliamps = self._effective_ma
        if milliamps is None:
            return None
        return self._label(self._step_index_for_ma(milliamps))

    async def async_select_option(self, option: str) -> None:
        """Imposta lo step scelto."""
        try:
            index = self.options.index(option)
        except ValueError as err:
            raise ServiceValidationError(
                f"'{option}' non e' uno step di potenza valido"
            ) from err
        await self._async_send_step(self._steps[index][1])
