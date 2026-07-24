"""Costanti per l'integrazione Daze Cloud."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "daze_cloud"

# --- Configurazione cloud Daze (produzione) -------------------------------
# Valori ricavati dagli appsettings di produzione dell'app ufficiale.
# Il ClientId Cognito e' un identificativo pubblico dell'app, non un segreto.
AWS_REGION: Final = "eu-central-1"
COGNITO_CLIENT_ID: Final = "6g7tg5gqia5o0r8cnq06e4cj6m"
COGNITO_ENDPOINT: Final = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/"
WEBAPI_BASE_URL: Final = "https://webapi.dazeservice.com/"

# --- Chiavi config entry ---------------------------------------------------
CONF_EVSE_SERIAL: Final = "evse_serial"
CONF_SOCKET_SERIAL: Final = "socket_serial"
CONF_EVSE_NAME: Final = "evse_name"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_MIN_CURRENT: Final = "min_current"
CONF_MAX_CURRENT: Final = "max_current"

DEFAULT_SCAN_INTERVAL: Final = 30  # secondi
DEFAULT_MIN_CURRENT: Final = 6  # A - minimo di norma per la ricarica AC
DEFAULT_MAX_CURRENT: Final = 32  # A

# Tensione nominale usata dall'app Daze per convertire potenza <-> corrente.
# Verificata sui valori dell'app: 1,5 kW = 6,52 A, 2,5 kW = 10869 mA, ecc.
# L'app tronca (floor) i milliampere risultanti, non li arrotonda.
NOMINAL_VOLTAGE: Final = 230

# --- Scala dei limiti di ricarica ------------------------------------------
# L'app Daze non offre una potenza continua ma una scala di step fissi. Se sulla
# wallbox si imposta una corrente che non corrisponde a uno step, l'app smette di
# aggiornare la potenza mostrata e resta disallineata: per questo ogni scrittura
# viene agganciata a questa scala.
#
# Coppie (kW monofase, milliampere inviati all'API). I kW sono le etichette
# esatte dell'app; su impianto trifase la stessa corrente vale il triplo della
# potenza, quindi l'etichetta va moltiplicata per il numero di fasi.
#
# I milliampere seguono la conversione dell'app - floor(W * 1000 / 230) - su
# tutti gli step tranne l'ultimo: 7,4 kW darebbe 32173 mA ma l'app si ferma a
# 32,00 A, cioe' al massimo di targa della wallbox.
CHARGING_POWER_STEPS: Final[tuple[tuple[float, int], ...]] = (
    (1.5, 6521),
    (1.7, 7391),
    (1.9, 8260),
    (2.1, 9130),
    (2.3, 10000),
    (2.5, 10869),
    (2.7, 11739),
    (2.9, 12608),
    (3.1, 13478),
    (3.3, 14347),
    (3.5, 15217),
    (3.7, 16086),
    (3.9, 16956),
    (4.1, 17826),
    (4.3, 18695),
    (4.5, 19565),
    (4.7, 20434),
    (4.9, 21304),
    (5.1, 22173),
    (5.3, 23043),
    (5.5, 23913),
    (5.7, 24782),
    (5.9, 25652),
    (6.1, 26521),
    (6.3, 27391),
    (6.5, 28260),
    (6.7, 29130),
    (6.9, 30000),
    (7.1, 30869),
    (7.3, 31739),
    (7.4, 32000),
)

# Le sole correnti (in mA) accettate dalla wallbox senza disallineare l'app.
CHARGING_CURRENT_STEPS_MA: Final[tuple[int, ...]] = tuple(
    milliamps for _, milliamps in CHARGING_POWER_STEPS
)

# Passo dello slider, monofase. Volutamente 0,1 e non 0,2 (il passo della scala):
# il frontend di Home Assistant calcola le posizioni del cursore come multipli di
# `step` a partire da **zero**, non da `min`. Con 0,2 sarebbero raggiungibili solo
# 6,0 / 6,2 / 6,4 ..., mentre la scala Daze e' sfalsata di 0,1 (1,5 / 1,7 / 6,1 /
# 6,3 ...): meta' dei valori resterebbe irraggiungibile. Con 0,1 ogni step cade su
# una posizione del cursore - e vale anche in trifase, dove la scala diventa
# 4,5 / 5,1 / 5,7 ... , tutti multipli di 0,3 = 0,1 * 3.
CHARGING_POWER_SLIDER_STEP_KW: Final = 0.1

# --- Stati EVSE (enum EVSEState dell'app) ----------------------------------
EVSE_STATE_UNKNOWN: Final = 0
EVSE_STATE_STANDBY: Final = 1
EVSE_STATE_EV_CONNECTED_WAIT_AUTH: Final = 2
EVSE_STATE_CHARGING: Final = 3
EVSE_STATE_ERROR: Final = 4
EVSE_STATE_EV_CONNECTED_AUTHORIZED: Final = 5
EVSE_STATE_EV_CONNECTED_WAIT_POWER: Final = 6
EVSE_STATE_PREPARING: Final = 7
EVSE_STATE_UNAVAILABLE: Final = 8
EVSE_STATE_FINISHING: Final = 9
EVSE_STATE_RESERVED: Final = 10
EVSE_STATE_SCHEDULED_PAUSE: Final = 100
EVSE_STATE_SMART_TARIFF_PAUSE: Final = 101

EVSE_STATE_NAMES: Final[dict[int, str]] = {
    EVSE_STATE_UNKNOWN: "unknown",
    EVSE_STATE_STANDBY: "standby",
    EVSE_STATE_EV_CONNECTED_WAIT_AUTH: "waiting_authorization",
    EVSE_STATE_CHARGING: "charging",
    EVSE_STATE_ERROR: "error",
    EVSE_STATE_EV_CONNECTED_AUTHORIZED: "authorized",
    EVSE_STATE_EV_CONNECTED_WAIT_POWER: "waiting_power",
    EVSE_STATE_PREPARING: "preparing",
    EVSE_STATE_UNAVAILABLE: "unavailable",
    EVSE_STATE_FINISHING: "finishing",
    EVSE_STATE_RESERVED: "reserved",
    EVSE_STATE_SCHEDULED_PAUSE: "scheduled_pause",
    EVSE_STATE_SMART_TARIFF_PAUSE: "smart_tariff_pause",
}

# Stati in cui un veicolo risulta fisicamente collegato.
EVSE_STATES_VEHICLE_CONNECTED: Final = frozenset(
    {
        EVSE_STATE_EV_CONNECTED_WAIT_AUTH,
        EVSE_STATE_CHARGING,
        EVSE_STATE_EV_CONNECTED_AUTHORIZED,
        EVSE_STATE_EV_CONNECTED_WAIT_POWER,
        EVSE_STATE_PREPARING,
        EVSE_STATE_FINISHING,
    }
)
