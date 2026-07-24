"""Entita' base condivisa dalle piattaforme Daze Cloud."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DazeRuntimeData
from .api import DazeError
from .const import DOMAIN
from .coordinator import DazeCoordinator

# Scarto tollerato tra il valore inviato e quello riletto dal cloud, in mA:
# l'API restituisce la corrente arrotondata a 2 decimali (10869 -> 10,87 A).
_READBACK_TOLERANCE_MA = 10


class DazeEntity(CoordinatorEntity[DazeCoordinator]):
    """Base per tutte le entita' Daze: device info e attributi comuni."""

    _attr_has_entity_name = True

    def __init__(self, runtime: DazeRuntimeData, key: str) -> None:
        """Inizializza l'entita' agganciandola al device della wallbox."""
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.evse_serial}_{key}"
        state = runtime.coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.evse_serial)},
            name=runtime.evse_name,
            manufacturer="Daze",
            model="Dazebox",
            serial_number=runtime.evse_serial,
            sw_version=state.software_version if state else None,
            hw_version=state.firmware_version if state else None,
        )

    @property
    def _state(self):
        """Scorciatoia allo stato letto dal coordinator."""
        return self.coordinator.data


class DazeChargingLimitEntity(DazeEntity):
    """Base dei controlli sul limite di ricarica.

    Corrente, potenza e tendina degli step sono viste diverse della stessa
    impostazione (`maxExternalChargingCurrent`), che accetta solo i valori
    discreti della scala Daze. Qui vivono la lettura del valore corrente e
    l'invio di uno step, cosi' le piattaforme non possono divergere.
    """

    def __init__(
        self, runtime: DazeRuntimeData, key: str, steps: tuple[tuple[float, int], ...]
    ) -> None:
        """Memorizza la scala di step utilizzabile da questo controllo."""
        super().__init__(runtime, key)
        self._steps = steps
        # Step inviato di recente, mostrato finche' il cloud non lo riflette.
        self._pending_ma: int | None = None

    @property
    def _phases(self) -> int:
        """Numero di fasi della wallbox (1 su impianto monofase)."""
        if self._state is not None and self._state.three_phase:
            return 3
        return 1

    @property
    def _current_ma(self) -> int | None:
        """Corrente massima attualmente impostata sulla wallbox, in mA."""
        if self._state is None or self._state.max_current_a is None:
            return None
        return int(round(self._state.max_current_a * 1000))

    @property
    def _effective_ma(self) -> int | None:
        """Corrente da mostrare: quella inviata finche' il cloud non si allinea."""
        current = self._current_ma
        if self._pending_ma is None:
            return current
        if (
            current is not None
            and abs(current - self._pending_ma) <= _READBACK_TOLERANCE_MA
        ):
            self._pending_ma = None
            return current
        return self._pending_ma

    def _step_index_for_ma(self, milliamps: float) -> int:
        """Indice dello step piu' vicino alla corrente indicata."""
        return min(
            range(len(self._steps)),
            key=lambda i: (abs(self._steps[i][1] - milliamps), self._steps[i][1]),
        )

    def _kw(self, index: int) -> float:
        """Potenza dello step indicato, scalata sul numero di fasi."""
        return round(self._steps[index][0] * self._phases, 2)

    async def _async_send_step(self, milliamps: int) -> None:
        """Invia uno step alla wallbox e ne mostra subito l'effetto."""
        try:
            await self._runtime.client.async_set_max_current_ma(
                self._runtime.evse_serial,
                self._runtime.socket_serial,
                milliamps,
            )
        except DazeError as err:
            raise HomeAssistantError(
                f"Impostazione del limite di ricarica fallita: {err}"
            ) from err
        self._pending_ma = milliamps
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
