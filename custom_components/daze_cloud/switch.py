"""Switch per avviare e fermare la ricarica."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DazeConfigEntry, DazeRuntimeData
from .api import DazeError
from .const import EVSE_STATE_CHARGING
from .entity import DazeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DazeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crea gli switch della wallbox."""
    runtime = entry.runtime_data
    async_add_entities([DazeChargingSwitch(runtime), DazeAutostartSwitch(runtime)])


class DazeChargingSwitch(DazeEntity, SwitchEntity):
    """Avvia/ferma la sessione di ricarica."""

    entity_description = SwitchEntityDescription(
        key="charging",
        translation_key="charging",
    )

    def __init__(self, runtime: DazeRuntimeData) -> None:
        """Inizializza lo switch."""
        super().__init__(runtime, "charging_switch")

    @property
    def is_on(self) -> bool | None:
        """True se la wallbox sta erogando."""
        if self._state is None:
            return None
        return self._state.evse_state == EVSE_STATE_CHARGING

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Avvia la ricarica (riprende se era in pausa)."""
        client = self._runtime.client
        socket = self._runtime.socket_serial
        try:
            if self._state is not None and self._state.is_paused:
                await client.async_play_charge(socket)
            else:
                await client.async_start_charge(socket)
        except DazeError as err:
            raise HomeAssistantError(f"Avvio ricarica fallito: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Ferma la ricarica."""
        try:
            await self._runtime.client.async_stop_charge(self._runtime.socket_serial)
        except DazeError as err:
            raise HomeAssistantError(f"Stop ricarica fallito: {err}") from err
        await self.coordinator.async_request_refresh()


class DazeAutostartSwitch(DazeEntity, SwitchEntity):
    """Avvio automatico della ricarica quando si collega il veicolo."""

    entity_description = SwitchEntityDescription(
        key="autostart",
        translation_key="autostart",
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, runtime: DazeRuntimeData) -> None:
        """Inizializza lo switch autostart."""
        super().__init__(runtime, "autostart")

    @property
    def is_on(self) -> bool | None:
        """Stato dell'autostart letto dalla configurazione della wallbox."""
        if self._state is None:
            return None
        return self._state.autostart

    async def _async_set(self, enabled: bool) -> None:
        """Applica il nuovo valore di autostart."""
        try:
            await self._runtime.client.async_set_autostart(
                self._runtime.evse_serial, enabled
            )
        except DazeError as err:
            raise HomeAssistantError(f"Modifica autostart fallita: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Attiva l'autostart."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disattiva l'autostart."""
        await self._async_set(False)
