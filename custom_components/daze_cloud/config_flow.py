"""Config flow per l'integrazione Daze Cloud."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    ConfigEntry,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DazeApiError, DazeAuthError, DazeCloudClient, DazeEvse
from .const import (
    CONF_EVSE_NAME,
    CONF_EVSE_SERIAL,
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    CONF_SCAN_INTERVAL,
    CONF_SOCKET_SERIAL,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class DazeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Gestisce il flusso di configurazione di Daze Cloud."""

    VERSION = 1

    def __init__(self) -> None:
        """Inizializza lo stato del flow."""
        self._username: str = ""
        self._password: str = ""
        self._evses: list[DazeEvse] = []

    async def _async_authenticate(
        self, username: str, password: str
    ) -> tuple[list[DazeEvse] | None, str | None]:
        """Verifica le credenziali e recupera le wallbox disponibili."""
        client = DazeCloudClient(
            async_get_clientsession(self.hass), username, password
        )
        try:
            await client.async_login()
            evses = await client.async_get_evses()
        except DazeAuthError:
            return None, "invalid_auth"
        except DazeApiError as err:
            _LOGGER.debug("Errore API durante il setup: %s", err)
            return None, "cannot_connect"
        if not evses:
            return None, "no_evses"
        return evses, None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Primo passo: credenziali dell'account Daze."""
        errors: dict[str, str] = {}

        if user_input is not None:
            evses, error = await self._async_authenticate(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                self._username = user_input[CONF_USERNAME]
                self._password = user_input[CONF_PASSWORD]
                self._evses = evses or []
                if len(self._evses) == 1:
                    return await self._async_create(self._evses[0])
                return await self.async_step_select_evse()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_select_evse(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Secondo passo: scelta della wallbox se l'account ne ha piu' di una."""
        if user_input is not None:
            serial = user_input[CONF_EVSE_SERIAL]
            evse = next(e for e in self._evses if e.serial_number == serial)
            return await self._async_create(evse)

        options = {e.serial_number: f"{e.name} ({e.serial_number})" for e in self._evses}
        return self.async_show_form(
            step_id="select_evse",
            data_schema=vol.Schema({vol.Required(CONF_EVSE_SERIAL): vol.In(options)}),
        )

    async def _async_create(self, evse: DazeEvse) -> ConfigFlowResult:
        """Crea la config entry per la wallbox scelta."""
        await self.async_set_unique_id(evse.serial_number)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=evse.name,
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_EVSE_SERIAL: evse.serial_number,
                CONF_SOCKET_SERIAL: evse.socket_serial,
                CONF_EVSE_NAME: evse.name,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Avvia la riautenticazione quando le credenziali smettono di funzionare."""
        self._username = entry_data[CONF_USERNAME]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Chiede di nuovo la password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            _, error = await self._async_authenticate(
                self._username, user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": self._username},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DazeOptionsFlow:
        """Restituisce il flow delle opzioni."""
        return DazeOptionsFlow()


class DazeOptionsFlow(OptionsFlow):
    """Opzioni: intervallo di polling e limiti di corrente."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Mostra e salva le opzioni."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=10, max=600)),
                vol.Optional(
                    CONF_MIN_CURRENT,
                    default=options.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT),
                ): vol.All(int, vol.Range(min=6, max=32)),
                vol.Optional(
                    CONF_MAX_CURRENT,
                    default=options.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT),
                ): vol.All(int, vol.Range(min=6, max=32)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
