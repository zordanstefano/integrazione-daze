#!/usr/bin/env python3
"""Sonda gli endpoint utili a energia totale e sessioni reali (senza Home Assistant).

Fa il login, individua wallbox + network, e dumpa in JSON grezzo:
  - lo stato attuale del connettore (remoteInfo), per vedere lo stato fisico ora;
  - lo storico sessioni v4/evses/{serial}/rechargeSessions (schema degli items);
  - il consumo aggregato v3/networks/{uid}/evses/energy su un intervallo,
    candidato per il sensore "energia totale".

Uso:
    python3 tools/probe_sessions.py --username tua@email.it
    (la password viene chiesta a runtime e non viene mai stampata)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import aiohttp
except ImportError:
    sys.exit("Manca aiohttp: pip install aiohttp")

_COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "daze_cloud"
)


def _load_api() -> types.ModuleType:
    """Carica api.py fuori dal package HA (come in test_daze_api.py)."""
    pkg = types.ModuleType("_daze_standalone")
    pkg.__path__ = [str(_COMPONENT_DIR)]
    sys.modules["_daze_standalone"] = pkg
    for name in ("const", "api"):
        spec = importlib.util.spec_from_file_location(
            f"_daze_standalone.{name}", _COMPONENT_DIR / f"{name}.py"
        )
        if spec is None or spec.loader is None:
            sys.exit(f"Impossibile caricare {name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"_daze_standalone.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["_daze_standalone.api"]


_api = _load_api()
DazeCloudClient = _api.DazeCloudClient
DazeError = _api.DazeError


def _dump(label: str, value: object) -> None:
    print(f"\n===== {label} =====")
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


async def _try(label: str, coro) -> object | None:
    """Esegue una richiesta mostrando l'esito senza far cadere lo script."""
    try:
        result = await coro
    except DazeError as err:
        print(f"\n===== {label} =====\n  ERRORE: {err}")
        return None
    _dump(label, result)
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description="Sonda sessioni/energia Daze")
    parser.add_argument("--username", required=True, help="Email account Daze")
    parser.add_argument(
        "--days", type=int, default=30, help="Ampiezza in giorni per evses/energy"
    )
    args = parser.parse_args()

    # Comodo per l'esecuzione non interattiva (senza TTY): la password puo'
    # arrivare da DAZE_PASSWORD, altrimenti viene chiesta a runtime.
    password = os.environ.get("DAZE_PASSWORD") or getpass.getpass("Password Daze: ")

    async with aiohttp.ClientSession() as session:
        client = DazeCloudClient(session, args.username, password)

        print("[1] Login...")
        await client.async_login()
        print(f"    OK - {client.email}")

        # Network uid + wallbox, rifacendo la discovery di async_get_evses.
        networks = await client._request(
            "GET", f"v3/users/{client.email}/networks?includeStats=false"
        )
        uid = None
        for network in networks or []:
            uid = (
                network.get("uid")
                or network.get("networkUid")
                or network.get("id")
            )
            if uid:
                break
        print(f"    network uid: {uid}")

        evses = await client.async_get_evses()
        if not evses:
            print("Nessuna wallbox.")
            return 1
        target = evses[0]
        serial = target.serial_number
        socket = target.socket_serial
        print(f"    wallbox: {target.name}  serial={serial}  socket={socket}")

        # Stato fisico attuale, grezzo: interessa evseState + chargeSession.
        state = await client.async_get_state(serial, socket)
        _dump(
            "STATO ATTUALE (interpretato)",
            {
                "evse_state": state.evse_state,
                "session_id": state.session_id,
                "power_w": state.power_w,
                "energy_wh": state.energy_wh,
                "charge_seconds": state.charge_seconds,
            },
        )
        _dump("remoteInfo GREZZO", state.raw)

        # Storico sessioni: schema degli items (ora che una ricarica vera e' avvenuta).
        await _try(
            "v4/evses/{serial}/rechargeSessions",
            client._request("GET", f"v4/evses/{serial}/rechargeSessions"),
        )

        # Consumo aggregato della wallbox: candidato per l'energia totale.
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=args.days)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        qs = (
            f"startUtc={start.strftime(fmt)}"
            f"&endUtc={end.strftime(fmt)}"
            f"&timeIntervalMinutes=1440"
        )
        await _try(
            f"v3/networks/{{uid}}/evses/energy (ultimi {args.days} g)",
            client._request("GET", f"v3/networks/{uid}/evses/energy?{qs}"),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
