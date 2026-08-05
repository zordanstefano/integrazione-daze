"""Coordinator per il polling dello stato della wallbox Daze."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    DazeApiError,
    DazeAuthError,
    DazeCloudClient,
    DazeLastSession,
    DazeSocketState,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Versione del formato salvato per l'ultima sessione.
_STORE_VERSION = 1


def _last_session_to_store(session: DazeLastSession) -> dict[str, Any]:
    """Serializza l'ultima sessione per la persistenza (datetime come ISO)."""
    return {
        "session_id": session.session_id,
        "energy_wh": session.energy_wh,
        "charge_seconds": session.charge_seconds,
        "start": session.start.isoformat() if session.start else None,
        "end": session.end.isoformat() if session.end else None,
    }


def _last_session_from_store(data: dict[str, Any]) -> DazeLastSession | None:
    """Ricostruisce l'ultima sessione dai dati salvati, tollerando formati vecchi."""
    if not isinstance(data, dict):
        return None
    return DazeLastSession(
        session_id=data.get("session_id"),
        energy_wh=data.get("energy_wh"),
        charge_seconds=data.get("charge_seconds"),
        start=dt_util.parse_datetime(data["start"]) if data.get("start") else None,
        end=dt_util.parse_datetime(data["end"]) if data.get("end") else None,
    )


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
        # Ultima sessione conclusa (persistita) e sessione attualmente aperta.
        self.last_session: DazeLastSession | None = None
        self._live_session: DazeLastSession | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, _STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.last_session"
        )

    async def async_load_last_session(self) -> None:
        """Carica dal disco l'ultima sessione, cosi' sopravvive ai riavvii."""
        data = await self._store.async_load()
        if data:
            self.last_session = _last_session_from_store(data)

    async def _async_update_data(self) -> DazeSocketState:
        """Legge lo stato corrente dal cloud Daze."""
        try:
            state = await self.client.async_get_state(
                self.evse_serial, self.socket_serial
            )
        except DazeAuthError as err:
            # Fa ripartire il flusso di riautenticazione in HA.
            raise ConfigEntryAuthFailed(str(err)) from err
        except DazeApiError as err:
            raise UpdateFailed(str(err)) from err

        await self._track_session(state)
        state.last_session = self.last_session
        return state

    async def _track_session(self, state: DazeSocketState) -> None:
        """Aggiorna l'ultima sessione osservando apertura/chiusura del chargeSession.

        La sessione porta valori assoluti (energia, durata, inizio): lo snapshot
        "vivo" si ricostruisce da solo anche dopo un riavvio a sessione aperta.
        Alla chiusura (cavo staccato) la sessione viene promossa a "ultima".
        """
        session_id = state.session_id
        if session_id is not None:
            if (
                self._live_session is not None
                and self._live_session.session_id != session_id
            ):
                # Nuova sessione senza passare da "cavo staccato": chiudi la vecchia.
                await self._finalize(self._live_session)
            self._live_session = DazeLastSession(
                session_id=session_id,
                energy_wh=state.energy_wh,
                charge_seconds=state.charge_seconds,
                start=state.session_start,
            )
        elif self._live_session is not None:
            # chargeSession sparito: il cavo e' stato staccato, sessione conclusa.
            await self._finalize(self._live_session)
            self._live_session = None

    async def _finalize(self, live: DazeLastSession) -> None:
        """Promuove una sessione conclusa a "ultima sessione" e la persiste.

        Scarta le sessioni fantasma - solo l'handshake al collegamento, nessuna
        energia erogata - cosi' non soppiantano l'ultima sessione reale.
        """
        if not live.energy_wh or live.energy_wh <= 0:
            return
        self.last_session = DazeLastSession(
            session_id=live.session_id,
            energy_wh=live.energy_wh,
            charge_seconds=live.charge_seconds,
            start=live.start,
            end=dt_util.utcnow(),
        )
        await self._store.async_save(_last_session_to_store(self.last_session))
