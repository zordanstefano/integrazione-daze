"""Number per impostare il limite di ricarica in potenza."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DazeConfigEntry, DazeRuntimeData
from .api import allowed_steps
from .const import (
    CHARGING_POWER_SLIDER_STEP_KW,
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
    """Crea il controllo della potenza massima."""
    options = entry.options
    steps = allowed_steps(
        options.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT),
        options.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT),
    )
    async_add_entities([DazeMaxPowerNumber(entry.runtime_data, steps)])


class DazeMaxPowerNumber(DazeChargingLimitEntity, NumberEntity):
    """Limite di ricarica espresso in potenza.

    E' la stessa impostazione della corrente massima, vista come la mostra l'app
    ufficiale. L'app espone una scala fissa (1,5 -> 7,3 kW a passi di 0,2 kW piu'
    un ultimo step da 7,4 kW) e ignora la potenza se la corrente non coincide con
    uno step: qui accettiamo quindi solo quei valori.

    Chi vuole scegliere lo step senza passare dal cursore ha la tendina omonima
    nel dominio `select`, che espone gli stessi valori senza slittamenti.
    """

    entity_description = NumberEntityDescription(
        key="max_power",
        translation_key="max_power",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
    )
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, runtime: DazeRuntimeData, steps: tuple[tuple[float, int], ...]
    ) -> None:
        """Inizializza il controllo sulla scala di step ammessa."""
        super().__init__(runtime, "max_power", steps)

    @property
    def native_min_value(self) -> float:
        """Potenza del primo step ammesso."""
        return self._kw(0)

    @property
    def native_max_value(self) -> float:
        """Potenza dell'ultimo step ammesso."""
        return self._kw(-1)

    @property
    def native_step(self) -> float:
        """Passo del cursore: mezzo passo della scala, per raggiungerla tutta.

        Vedi `CHARGING_POWER_SLIDER_STEP_KW`: con il passo pieno il frontend
        offrirebbe solo una posizione su due della scala.
        """
        return round(CHARGING_POWER_SLIDER_STEP_KW * self._phases, 2)

    @property
    def native_value(self) -> float | None:
        """Potenza corrispondente alla corrente massima impostata.

        La corrente letta viene ricondotta al suo step, cosi' il valore mostrato
        e' sempre uno di quelli della scala anche se la wallbox e' stata portata
        fuori scala da un altro client.
        """
        milliamps = self._effective_ma
        if milliamps is None:
            return None
        return self._kw(self._step_index_for_ma(milliamps))

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        """Elenca le potenze ammesse, utile per le automazioni."""
        return {"valori_ammessi": [self._kw(i) for i in range(len(self._steps))]}

    async def async_set_native_value(self, value: float) -> None:
        """Invia il nuovo limite scegliendo lo step di potenza piu' vicino."""
        target = value / max(self._phases, 1)
        index = min(
            range(len(self._steps)),
            key=lambda i: (abs(self._steps[i][0] - target), self._steps[i][0]),
        )
        await self._async_send_step(self._steps[index][1])
