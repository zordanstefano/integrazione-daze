"""Coordinator per il polling dello stato della wallbox Daze."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DazeApiError, DazeAuthError, DazeCloudClient, DazeSocketState
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class DazeCoordinator(DataUpdateCoordinator[DazeSocketState]):
    """Interroga periodicamente l'API cloud per lo stato del connettore."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: DazeCloudClient,
        evse_serial: str,
        socket_serial: str,
        scan_interval: int,
    ) -> None:
        """Inizializza il coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            config_entry=entry,
        )
        self.client = client
        self.evse_serial = evse_serial
        self.socket_serial = socket_serial

    async def _async_update_data(self) -> DazeSocketState:
        """Legge lo stato corrente dal cloud Daze."""
        try:
            return await self.client.async_get_state(
                self.evse_serial, self.socket_serial
            )
        except DazeAuthError as err:
            # Fa ripartire il flusso di riautenticazione in HA.
            raise ConfigEntryAuthFailed(str(err)) from err
        except DazeApiError as err:
            raise UpdateFailed(str(err)) from err
