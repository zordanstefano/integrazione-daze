"""Integrazione Daze Cloud per Home Assistant."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    Platform,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from .api import DazeApiError, DazeAuthError, DazeCloudClient
from .const import (
    CONF_EVSE_NAME,
    CONF_EVSE_SERIAL,
    CONF_SCAN_INTERVAL,
    CONF_SOCKET_SERIAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import DazeCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Entita' non piu' create dall'integrazione, come (piattaforma, suffisso unique_id).
# Senza questa pulizia resterebbero nel registro, orfane e perennemente
# "non disponibile".
#
# - la corrente massima e' passata da `number` scrivibile a sensore in sola
#   lettura: si imposta solo in potenza, dove la scala di step e' rispettata;
# - la tendina e' passata da `power_step` a `max_power`: cambiare l'unique_id la
#   fa ricreare, cosi' anche l'entity_id segue il nome nuovo (rinominare la sola
#   traduzione avrebbe lasciato `select.*_step_di_potenza`).
REMOVED_ENTITIES: tuple[tuple[Platform, str], ...] = (
    (Platform.NUMBER, "max_current"),
    (Platform.SELECT, "power_step"),
)

# Sensori la cui unita' e' cambiata dopo la prima installazione, come
# (suffisso unique_id, unita' vecchia, unita' nuova).
#
# Home Assistant **non** adotta la nuova unita' di un sensore gia' esistente: si
# tiene quella registrata alla creazione e converte il valore nativo, per non
# spezzare le statistiche storiche. Senza questa correzione la potenza resterebbe
# in W (1257,000 W invece di 1,257 kW) e l'energia in Wh.
UNIT_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("power", UnitOfPower.WATT, UnitOfPower.KILO_WATT),
    ("energy_session", UnitOfEnergy.WATT_HOUR, UnitOfEnergy.KILO_WATT_HOUR),
)

@dataclass(slots=True)
class DazeRuntimeData:
    """Dati condivisi tra le piattaforme dell'integrazione."""

    coordinator: DazeCoordinator
    client: DazeCloudClient
    evse_serial: str
    socket_serial: str
    evse_name: str


type DazeConfigEntry = ConfigEntry[DazeRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: DazeConfigEntry) -> bool:
    """Configura una wallbox Daze da una config entry."""
    session = async_get_clientsession(hass)
    client = DazeCloudClient(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    try:
        await client.async_login()
    except DazeAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except DazeApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = DazeCoordinator(
        hass,
        entry,
        client,
        entry.data[CONF_EVSE_SERIAL],
        entry.data[CONF_SOCKET_SERIAL],
        scan_interval,
    )
    await coordinator.async_load_last_session()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = DazeRuntimeData(
        coordinator=coordinator,
        client=client,
        evse_serial=entry.data[CONF_EVSE_SERIAL],
        socket_serial=entry.data[CONF_SOCKET_SERIAL],
        evse_name=entry.data.get(CONF_EVSE_NAME, entry.data[CONF_EVSE_SERIAL]),
    )

    _async_cleanup_removed_entities(hass, entry)
    _async_fix_doubled_entity_ids(hass, entry)
    _async_migrate_units(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _async_cleanup_removed_entities(
    hass: HomeAssistant, entry: DazeConfigEntry
) -> None:
    """Toglie dal registro le entita' che l'integrazione non crea piu'."""
    registry = er.async_get(hass)
    evse_serial = entry.data[CONF_EVSE_SERIAL]
    for platform, suffix in REMOVED_ENTITIES:
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, f"{evse_serial}_{suffix}"
        )
        if entity_id:
            _LOGGER.debug("Rimuovo l'entita' obsoleta %s", entity_id)
            registry.async_remove(entity_id)


def _async_fix_doubled_entity_ids(
    hass: HomeAssistant, entry: DazeConfigEntry
) -> None:
    """Corregge gli entity_id nati col nome del device duplicato.

    Alcune entita' aggiunte a un device gia' esistente nascono con l'entity_id
    che ripete il nome del device (`sensor.wallbox_wallbox_...`): l'id e' congelato
    alla creazione, quando la traduzione del nome non e' ancora risolvibile, e HA
    vi antepone il device una seconda volta. Qui l'id viene ricondotto a
    `<dev>_...`, senza toccare l'unique_id (storico e personalizzazioni restano).

    Scorre tutte le entita' del config entry, non un elenco fisso: cosi' anche le
    entita' aggiunte in futuro si auto-correggono al riavvio successivo. Agisce
    solo sul prefisso davvero raddoppiato, cosi' non tocca id gia' rinominati
    dall'utente; la rinomina e' saltata se il nuovo id e' gia' occupato.
    Idempotente e no-op sulle installazioni pulite.
    """
    registry = er.async_get(hass)
    device_slug = slugify(entry.data.get(CONF_EVSE_NAME, entry.data[CONF_EVSE_SERIAL]))
    doubled_prefix = f"{device_slug}_{device_slug}_"
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        entity_id = registry_entry.entity_id
        domain, _, object_id = entity_id.partition(".")
        if not object_id.startswith(doubled_prefix):
            continue
        # Toglie una sola occorrenza del nome del device dal prefisso.
        new_entity_id = f"{domain}.{object_id[len(device_slug) + 1:]}"
        if registry.async_get(new_entity_id) is not None:
            _LOGGER.warning(
                "Non rinomino %s: %s e' gia' in uso", entity_id, new_entity_id
            )
            continue
        _LOGGER.debug("Rinomino %s -> %s", entity_id, new_entity_id)
        registry.async_update_entity(entity_id, new_entity_id=new_entity_id)


def _async_migrate_units(hass: HomeAssistant, entry: DazeConfigEntry) -> None:
    """Allinea l'unita' registrata dei sensori a quella attuale dell'integrazione.

    Interviene solo dove l'unita' e' rimasta quella vecchia e l'utente non ne ha
    scelta una a mano: una preferenza esplicita, che vive nelle options, va
    rispettata e non riscritta a ogni avvio.
    """
    registry = er.async_get(hass)
    evse_serial = entry.data[CONF_EVSE_SERIAL]
    for suffix, old_unit, new_unit in UNIT_MIGRATIONS:
        entity_id = registry.async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{evse_serial}_{suffix}"
        )
        if entity_id is None:
            continue
        registry_entry = registry.async_get(entity_id)
        if registry_entry is None or registry_entry.unit_of_measurement != old_unit:
            continue
        options = dict(registry_entry.options.get(Platform.SENSOR, {}))
        if options.get("unit_of_measurement"):
            continue
        _LOGGER.debug("Aggiorno l'unita' di %s: %s -> %s", entity_id, old_unit, new_unit)
        registry.async_update_entity_options(
            entity_id,
            Platform.SENSOR,
            {**options, "unit_of_measurement": new_unit},
        )


async def async_unload_entry(hass: HomeAssistant, entry: DazeConfigEntry) -> bool:
    """Rimuove una config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: DazeConfigEntry) -> None:
    """Ricarica l'integrazione quando cambiano le opzioni."""
    await hass.config_entries.async_reload(entry.entry_id)
