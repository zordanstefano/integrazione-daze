"""Client per l'API cloud Daze (webapi.dazeservice.com).

Autenticazione via AWS Cognito con flusso USER_PASSWORD_AUTH: non serve l'SDK AWS,
sono semplici chiamate JSON verso l'endpoint cognito-idp.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from .const import (
    CHARGING_CURRENT_STEPS_MA,
    CHARGING_POWER_STEPS,
    COGNITO_CLIENT_ID,
    COGNITO_ENDPOINT,
    NOMINAL_VOLTAGE,
    WEBAPI_BASE_URL,
)

_LOGGER = logging.getLogger(__name__)

_COGNITO_HEADERS = {
    "Content-Type": "application/x-amz-json-1.1",
}
_TARGET_INITIATE_AUTH = "AWSCognitoIdentityProviderService.InitiateAuth"

# Margine di sicurezza prima della scadenza del token, in secondi.
_TOKEN_EXPIRY_MARGIN = 60
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Energia totale: l'API non ha un contatore a vita, ma `evses/energy` con un
# intervallo pari all'intera durata restituisce un unico bucket = energia
# cumulata dallo `startUtc`. Partiamo da un'epoca precedente a qualunque Dazebox
# (prodotto recente): i giorni senza dati valgono 0. Il valore cresce in modo
# monotono, adatto a un sensore TOTAL_INCREASING.
_ENERGY_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)
# La rilettura e' costosa (aggrega anni di dati) e l'energia cambia lentamente:
# la teniamo in cache e la rinfreschiamo al piu' ogni pochi minuti.
_TOTAL_ENERGY_TTL = timedelta(minutes=5)


class DazeError(Exception):
    """Errore generico dell'integrazione Daze."""


class DazeAuthError(DazeError):
    """Credenziali non valide o sessione non rinnovabile."""


class DazeApiError(DazeError):
    """Errore restituito dall'API Daze."""


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Estrae il payload di un JWT senza verificarne la firma.

    La firma e' gia' garantita da Cognito su canale TLS: qui serve solo leggere
    le claim (in particolare l'email) per costruire le chiamate successive.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    # base64url senza padding
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return {}


def _unwrap(payload: Any) -> Any:
    """Estrae il contenuto dall'envelope {data, message, errors} dell'API Daze.

    Tutte le risposte della webapi sono incapsulate; se l'envelope segnala errori
    li solleviamo qui invece di lasciar propagare dati vuoti.
    """
    if not isinstance(payload, dict) or "data" not in payload:
        return payload
    if errors := payload.get("errors"):
        raise DazeApiError(f"L'API Daze ha segnalato errori: {errors}")
    return payload["data"]


def _pick(data: dict[str, Any] | None, *names: str, default: Any = None) -> Any:
    """Legge una chiave dal JSON ignorando maiuscole/minuscole.

    L'API non e' consistente tra PascalCase e camelCase a seconda dell'endpoint,
    quindi normalizziamo qui invece di sparpagliare varianti nel codice.
    """
    if not data:
        return default
    lowered = {k.lower(): v for k, v in data.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return default


@dataclass(slots=True)
class DazeEvse:
    """Una wallbox e i suoi connettori."""

    serial_number: str
    name: str
    socket_serial: str
    firmware_version: str | None = None
    software_version: str | None = None
    photovoltaic: bool = False
    three_phase: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DazeLastSession:
    """Istantanea dell'ultima sessione conclusa.

    Per Daze una "sessione" e' l'intero periodo con il cavo collegato, non la
    singola carica: energia e durata sono cumulate su quel periodo, e la sessione
    si chiude solo allo scollegamento del cavo. Popolata dal coordinator, che
    osserva l'apertura/chiusura del `chargeSession` (vedi coordinator.py).
    """

    session_id: int | None = None
    energy_wh: float | None = None
    charge_seconds: float | None = None
    start: datetime | None = None
    end: datetime | None = None


@dataclass(slots=True)
class DazeSocketState:
    """Stato della wallbox: remoteInfo del connettore + configurazione dell'EVSE."""

    active: bool = False
    evse_state: int = 0
    suspension_reason: int | None = None
    system_error: int | None = None
    is_paused: bool = False
    three_phase: bool = False
    # Sessione in corso: a riposo l'API restituisce chargeSession = null.
    session_id: int | None = None
    power_w: float | None = None
    energy_wh: float | None = None
    # Energia cumulata della wallbox (a vita), non legata alla sessione.
    total_energy_wh: float | None = None
    current_l1: float | None = None
    current_l2: float | None = None
    current_l3: float | None = None
    voltage_l1: float | None = None
    voltage_l2: float | None = None
    voltage_l3: float | None = None
    charge_seconds: float | None = None
    # Inizio della sessione in corso (startTime del chargeSession).
    session_start: datetime | None = None
    # Ultima sessione conclusa, tracciata dal coordinator.
    last_session: DazeLastSession | None = None
    # Dati letti dal contratto EVSE (non presenti in remoteInfo).
    max_current_a: float | None = None
    grid_current_l1: float | None = None
    grid_current_l2: float | None = None
    grid_current_l3: float | None = None
    scc_limit_a: float | None = None
    autostart: bool | None = None
    photovoltaic: bool | None = None
    firmware_version: str | None = None
    software_version: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    raw_evse: dict[str, Any] = field(default_factory=dict)


def _parse_datetime(value: Any) -> datetime | None:
    """Converte un timestamp ISO 8601 (con o senza 'Z') in datetime aware."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_duration(value: Any) -> float | None:
    """Converte una TimeSpan .NET ("HH:MM:SS" o "d.HH:MM:SS") in secondi."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    days = 0
    text = value
    if "." in text and text.count(":") == 2 and text.index(".") < text.index(":"):
        day_part, _, text = text.partition(".")
        try:
            days = int(day_part)
        except ValueError:
            days = 0
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (float(p) for p in parts)
    except ValueError:
        return None
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


class DazeCloudClient:
    """Client asincrono per l'API cloud Daze."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
    ) -> None:
        """Inizializza il client con le credenziali dell'account Daze."""
        self._session = session
        self._username = username
        self._password = password
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._email: str | None = None
        self._auth_lock = asyncio.Lock()
        # Cache: uid della rete e ultimo valore di energia totale con timestamp.
        self._network_uid: str | None = None
        self._total_energy_wh: float | None = None
        self._total_energy_at: datetime | None = None

    @property
    def email(self) -> str | None:
        """Email dell'utente autenticato (dalle claim dell'id token)."""
        return self._email

    # --- Autenticazione ----------------------------------------------------

    async def _cognito_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Esegue una chiamata InitiateAuth verso Cognito."""
        headers = {**_COGNITO_HEADERS, "X-Amz-Target": _TARGET_INITIATE_AUTH}
        try:
            async with self._session.post(
                COGNITO_ENDPOINT,
                headers=headers,
                data=json.dumps(payload),
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    error_type = ""
                    try:
                        error_type = json.loads(body).get("__type", "")
                    except (ValueError, json.JSONDecodeError):
                        pass
                    if resp.status == 400 and error_type in (
                        "NotAuthorizedException",
                        "UserNotFoundException",
                    ):
                        raise DazeAuthError("Credenziali Daze non valide")
                    raise DazeApiError(
                        f"Cognito ha risposto {resp.status}: {error_type or body[:200]}"
                    )
                return json.loads(body)
        except aiohttp.ClientError as err:
            raise DazeApiError(f"Errore di rete verso Cognito: {err}") from err

    def _store_tokens(self, result: dict[str, Any]) -> None:
        """Salva i token restituiti da Cognito."""
        self._access_token = result.get("AccessToken")
        # Nel refresh Cognito non restituisce un nuovo refresh token.
        if result.get("RefreshToken"):
            self._refresh_token = result["RefreshToken"]
        expires_in = int(result.get("ExpiresIn", 3600))
        self._expires_at = time.time() + expires_in - _TOKEN_EXPIRY_MARGIN
        if id_token := result.get("IdToken"):
            claims = _decode_jwt_claims(id_token)
            self._email = claims.get("email") or self._email

    async def async_login(self) -> None:
        """Autentica con username e password."""
        data = await self._cognito_call(
            {
                "AuthFlow": "USER_PASSWORD_AUTH",
                "ClientId": COGNITO_CLIENT_ID,
                "AuthParameters": {
                    "USERNAME": self._username,
                    "PASSWORD": self._password,
                },
            }
        )
        result = data.get("AuthenticationResult")
        if not result:
            # Es. NEW_PASSWORD_REQUIRED o MFA: non supportati.
            challenge = data.get("ChallengeName", "sconosciuta")
            raise DazeAuthError(
                f"Login non completato, richiesta challenge '{challenge}' non supportata"
            )
        self._store_tokens(result)
        _LOGGER.debug("Login Daze riuscito per %s", self._email or self._username)

    async def _async_refresh(self) -> None:
        """Rinnova l'access token; ricade sul login completo se non possibile."""
        if not self._refresh_token:
            await self.async_login()
            return
        try:
            data = await self._cognito_call(
                {
                    "AuthFlow": "REFRESH_TOKEN_AUTH",
                    "ClientId": COGNITO_CLIENT_ID,
                    "AuthParameters": {"REFRESH_TOKEN": self._refresh_token},
                }
            )
        except DazeAuthError:
            # Refresh token revocato o scaduto: si riparte dalle credenziali.
            self._refresh_token = None
            await self.async_login()
            return
        result = data.get("AuthenticationResult")
        if not result:
            await self.async_login()
            return
        self._store_tokens(result)

    async def _async_valid_token(self) -> str:
        """Restituisce un access token valido, rinnovandolo se necessario."""
        async with self._auth_lock:
            if not self._access_token:
                await self.async_login()
            elif time.time() >= self._expires_at:
                await self._async_refresh()
            if not self._access_token:
                raise DazeAuthError("Impossibile ottenere un access token")
            return self._access_token

    # --- Chiamate all'API --------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        retry_on_auth: bool = True,
    ) -> Any:
        """Esegue una richiesta autenticata verso la webapi Daze."""
        token = await self._async_valid_token()
        url = WEBAPI_BASE_URL + path.lstrip("/")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401 and retry_on_auth:
                    # Token rifiutato: forziamo un refresh e riproviamo una volta.
                    async with self._auth_lock:
                        await self._async_refresh()
                    return await self._request(
                        method, path, json_body=json_body, retry_on_auth=False
                    )
                text = await resp.text()
                if resp.status == 401:
                    raise DazeAuthError("Autenticazione rifiutata dall'API Daze")
                if resp.status >= 400:
                    raise DazeApiError(
                        f"{method} {path} ha restituito {resp.status}: {text[:200]}"
                    )
                if not text:
                    return None
                try:
                    payload = json.loads(text)
                except (ValueError, json.JSONDecodeError):
                    return text
                return _unwrap(payload)
        except aiohttp.ClientError as err:
            raise DazeApiError(f"Errore di rete su {path}: {err}") from err

    async def async_get_evses(self) -> list[DazeEvse]:
        """Elenca le wallbox accessibili dall'account."""
        if not self._email:
            await self._async_valid_token()
        if not self._email:
            raise DazeApiError("Email utente non disponibile dopo il login")

        networks = await self._request(
            "GET", f"v3/users/{self._email}/networks?includeStats=false"
        )
        evses: list[DazeEvse] = []
        seen: set[str] = set()
        for network in networks or []:
            uid = _pick(network, "uid", "networkUid", "id")
            if not uid:
                continue
            raw_evses = await self._request("GET", f"v3/networks/{uid}/evses")
            for raw in raw_evses or []:
                evse = self._parse_evse(raw)
                if evse and evse.serial_number not in seen:
                    seen.add(evse.serial_number)
                    evses.append(evse)
        return evses

    @staticmethod
    def _parse_evse(raw: dict[str, Any]) -> DazeEvse | None:
        """Costruisce un DazeEvse dal contratto dell'API."""
        serial = _pick(raw, "serialNumber")
        if not serial:
            return None
        sockets = _pick(raw, "sockets", default=[]) or []
        socket_serial = ""
        for socket in sockets:
            if _pick(socket, "isPrimary", default=False):
                socket_serial = _pick(socket, "serialNumber", default="")
                break
        if not socket_serial and sockets:
            socket_serial = _pick(sockets[0], "serialNumber", default="")
        # Su alcuni modelli il connettore condivide il seriale della wallbox.
        socket_serial = socket_serial or serial
        return DazeEvse(
            serial_number=serial,
            name=_pick(raw, "evseName", "name", default=serial),
            socket_serial=socket_serial,
            firmware_version=_pick(raw, "firmwareVersion"),
            software_version=_pick(raw, "softwareVersion"),
            photovoltaic=bool(_pick(raw, "photovoltaic", default=False)),
            three_phase=bool(_pick(raw, "evseIsThreePhase", default=False)),
            raw=raw,
        )

    async def async_get_evse_config(self, evse_serial: str) -> dict[str, Any]:
        """Legge il contratto EVSE (corrente impostata, correnti di rete, versioni)."""
        raw = await self._request("GET", f"v3/evses/{evse_serial}")
        return raw if isinstance(raw, dict) else {}

    async def _async_network_uid(self) -> str | None:
        """Uid della rete a cui appartiene la wallbox (memorizzato in cache)."""
        if self._network_uid:
            return self._network_uid
        if not self._email:
            await self._async_valid_token()
        if not self._email:
            return None
        networks = await self._request(
            "GET", f"v3/users/{self._email}/networks?includeStats=false"
        )
        for network in networks or []:
            uid = _pick(network, "uid", "networkUid", "id")
            if uid:
                self._network_uid = uid
                return uid
        return None

    async def _async_total_energy_wh(self) -> float | None:
        """Energia cumulata della wallbox, in Wh, con cache a scadenza.

        L'endpoint e' network-scoped (`evses/energy`): con una sola wallbox
        equivale alla sua energia; su reti multi-wallbox e' la somma di tutte.
        Un errore transitorio non azzera il sensore: si tiene l'ultimo valore.
        """
        now = datetime.now(timezone.utc)
        if (
            self._total_energy_at is not None
            and now - self._total_energy_at < _TOTAL_ENERGY_TTL
        ):
            return self._total_energy_wh

        uid = await self._async_network_uid()
        if not uid:
            return self._total_energy_wh

        span_minutes = int((now - _ENERGY_EPOCH).total_seconds() // 60) + 1
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        try:
            data = await self._request(
                "GET",
                f"v3/networks/{uid}/evses/energy"
                f"?startUtc={_ENERGY_EPOCH.strftime(fmt)}"
                f"&endUtc={now.strftime(fmt)}"
                f"&timeIntervalMinutes={span_minutes}",
            )
        except DazeApiError as err:
            _LOGGER.debug("Lettura energia totale fallita: %s", err)
            return self._total_energy_wh

        if isinstance(data, list) and data:
            value = _to_float(_pick(data[0], "energyWh"))
            if value is not None:
                self._total_energy_wh = value
                self._total_energy_at = now
        return self._total_energy_wh

    async def async_get_state(
        self, evse_serial: str, socket_serial: str
    ) -> DazeSocketState:
        """Legge lo stato completo: remoteInfo del connettore + config dell'EVSE."""
        raw = await self._request(
            "GET",
            f"v3/sockets/{socket_serial}/remoteInfo"
            "?includeEcoInfo=false&includeNextSchedule=false",
        )
        if not isinstance(raw, dict):
            raise DazeApiError("Risposta remoteInfo non valida")
        evse = await self.async_get_evse_config(evse_serial)

        # A riposo chargeSession e' null. Potenza ed energia le riportiamo comunque a 0
        # (numeri utili alla dashboard energia); corrente e tensione restano invece
        # assenti e verranno mostrate come "Non in carica" dal layer sensore.
        session = _pick(raw, "chargeSession") or {}
        idle = not session

        return DazeSocketState(
            total_energy_wh=await self._async_total_energy_wh(),
            active=bool(_pick(raw, "active", default=False)),
            evse_state=int(_pick(raw, "evseState", default=0) or 0),
            suspension_reason=_pick(raw, "evseSuspensionReason"),
            system_error=_pick(raw, "evseSystemError"),
            is_paused=bool(_pick(raw, "isPaused", default=False)),
            three_phase=bool(_pick(raw, "evseIsThreePhase", default=False)),
            session_id=_pick(session, "sessionId"),
            power_w=0.0 if idle else _to_float(_pick(session, "instantPowerAsWatt")),
            energy_wh=(
                0.0 if idle else _to_float(_pick(session, "deliveredEnergyAsWattHour"))
            ),
            # Come le correnti di rete, l'API le espone in milliampere.
            current_l1=_milliamps_to_amps(_pick(session, "lastChargingCurrentInstantL1")),
            current_l2=_milliamps_to_amps(_pick(session, "lastChargingCurrentInstantL2")),
            current_l3=_milliamps_to_amps(_pick(session, "lastChargingCurrentInstantL3")),
            voltage_l1=_to_float(_pick(session, "lastACVoltageL1")),
            voltage_l2=_to_float(_pick(session, "lastACVoltageL2")),
            voltage_l3=_to_float(_pick(session, "lastACVoltageL3")),
            charge_seconds=0.0 if idle else _parse_duration(_pick(session, "chargeTime")),
            session_start=_parse_datetime(_pick(session, "startTime")),
            max_current_a=_milliamps_to_amps(
                _pick(evse, "maxExternalChargingCurrentInMilliAmps")
            ),
            grid_current_l1=_milliamps_to_amps(
                _pick(evse, "lastSupplyGridInstantCurrentL1")
            ),
            grid_current_l2=_milliamps_to_amps(
                _pick(evse, "lastSupplyGridInstantCurrentL2")
            ),
            grid_current_l3=_milliamps_to_amps(
                _pick(evse, "lastSupplyGridInstantCurrentL3")
            ),
            scc_limit_a=_milliamps_to_amps(_pick(evse, "sccLimit")),
            autostart=_pick(evse, "autostart"),
            photovoltaic=_pick(evse, "photovoltaic"),
            firmware_version=_pick(evse, "firmwareVersion"),
            software_version=_pick(evse, "softwareVersion"),
            raw=raw,
            raw_evse=evse,
        )

    # --- Comandi -----------------------------------------------------------

    async def async_start_charge(self, socket_serial: str) -> None:
        """Avvia una sessione di ricarica."""
        await self._request("POST", f"v3/sockets/{socket_serial}/commands/startcharge")

    async def async_stop_charge(self, socket_serial: str) -> None:
        """Termina la sessione di ricarica."""
        await self._request("POST", f"v3/sockets/{socket_serial}/commands/stopcharge")

    async def async_play_charge(self, socket_serial: str) -> None:
        """Riprende una ricarica in pausa."""
        await self._request("POST", f"v3/sockets/{socket_serial}/commands/playcharge")

    async def async_set_autostart(self, evse_serial: str, enabled: bool) -> None:
        """Attiva o disattiva l'avvio automatico della ricarica."""
        await self._request(
            "POST",
            f"v3/evses/{evse_serial}/configurations/autostart",
            json_body={"Autostart": enabled},
        )

    async def async_set_max_current_ma(
        self, evse_serial: str, socket_serial: str, milliamps: int
    ) -> None:
        """Imposta la corrente massima di ricarica in milliampere (unita' dell'API).

        Primitiva grezza: invia il valore cosi' com'e'. Chi non ha gia' scelto uno
        step valido dovrebbe passare da `async_set_max_current` /
        `async_set_max_power`, che agganciano la richiesta alla scala dell'app.
        """
        await self._request(
            "POST",
            f"v3/evses/{evse_serial}/configurations/maxExternalChargingCurrent",
            json_body={
                "SocketSerialNumber": socket_serial,
                "MaxExternalChargingCurrentInMilliAmps": int(milliamps),
            },
        )

    async def async_set_max_current(
        self, evse_serial: str, socket_serial: str, amps: float
    ) -> None:
        """Imposta la corrente massima di ricarica, in ampere.

        Il valore viene agganciato allo step piu' vicino della scala Daze.
        """
        await self.async_set_max_current_ma(
            evse_serial, socket_serial, nearest_current_step_ma(amps * 1000)
        )

    async def async_set_max_power(
        self, evse_serial: str, socket_serial: str, watts: float, phases: int = 1
    ) -> None:
        """Imposta il limite di ricarica partendo dalla potenza.

        Converte come l'app ufficiale - milliampere = floor(W / (230 * fasi) * 1000) -
        e aggancia il risultato allo step piu' vicino della scala Daze.
        """
        await self.async_set_max_current_ma(
            evse_serial,
            socket_serial,
            nearest_current_step_ma(watts_to_milliamps(watts, phases)),
        )


def _to_float(value: Any) -> float | None:
    """Converte in float tollerando None e stringhe."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _milliamps_to_amps(value: Any) -> float | None:
    """Converte i milliampere usati dall'API in ampere."""
    amps = _to_float(value)
    return None if amps is None else round(amps / 1000, 2)


def watts_to_milliamps(watts: float, phases: int = 1) -> int:
    """Converte una potenza in milliampere, come fa l'app Daze.

    L'app usa una tensione nominale fissa di 230 V e **tronca** il risultato:
    2500 W monofase -> floor(2500 * 1000 / 230) = 10869 mA, valore osservato
    davvero sulla wallbox.
    """
    return math.floor(watts * 1000 / (NOMINAL_VOLTAGE * max(phases, 1)))


def allowed_steps(
    min_current: int, max_current: int
) -> tuple[tuple[float, int], ...]:
    """Filtra la scala Daze con i limiti di corrente configurati.

    Se i limiti escludono tutti gli step (configurazione incoerente) si torna
    alla scala completa: meglio un controllo largo che un'entita' inutilizzabile.
    """
    steps = tuple(
        step
        for step in CHARGING_POWER_STEPS
        if min_current * 1000 <= step[1] <= max_current * 1000
    )
    return steps or CHARGING_POWER_STEPS


def nearest_current_step_ma(
    milliamps: float, steps: Sequence[int] = CHARGING_CURRENT_STEPS_MA
) -> int:
    """Aggancia una corrente allo step piu' vicino tra quelli ammessi.

    La wallbox accetta qualunque valore, ma l'app Daze mostra la potenza solo se
    la corrente coincide con uno step della sua scala: fuori dagli step app e
    wallbox restano disallineate. Agganciamo quindi ogni scrittura alla scala.
    """
    if not steps:
        return int(round(milliamps))
    return min(steps, key=lambda step: (abs(step - milliamps), step))
