#!/usr/bin/env python3
"""Verifica end-to-end del client Daze Cloud, senza Home Assistant.

Esegue login su Cognito, elenca le wallbox dell'account e legge lo stato del
connettore primario. Utile per validare che l'API ricostruita funzioni davvero.

Uso:
    python3 tools/test_daze_api.py --username tua@email.it
    (la password viene chiesta a runtime e non viene mai stampata)

Opzionale, per provare anche i comandi (NON esegue nulla senza conferma):
    python3 tools/test_daze_api.py --username ... --test-command set-current --amps 10
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import sys
import types
from pathlib import Path

try:
    import aiohttp
except ImportError:
    sys.exit("Manca aiohttp: pip install aiohttp")

_COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "daze_cloud"
)


def _load_api() -> types.ModuleType:
    """Carica api.py senza passare dal package, che dipende da Home Assistant.

    Registriamo un package sintetico cosi' che l'import relativo `.const`
    dentro api.py continui a funzionare anche fuori da HA.
    """
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


async def main() -> int:
    """Esegue la verifica."""
    parser = argparse.ArgumentParser(description="Test API cloud Daze")
    parser.add_argument("--username", required=True, help="Email account Daze")
    parser.add_argument(
        "--test-command",
        choices=["start", "stop", "set-current"],
        help="Esegue anche un comando di scrittura (chiede conferma)",
    )
    parser.add_argument("--amps", type=float, default=10.0, help="Ampere per set-current")
    args = parser.parse_args()

    password = getpass.getpass("Password Daze: ")

    async with aiohttp.ClientSession() as session:
        client = DazeCloudClient(session, args.username, password)

        print("\n[1] Login su Cognito...")
        try:
            await client.async_login()
        except DazeError as err:
            print(f"    FALLITO: {err}")
            return 1
        print(f"    OK - autenticato come {client.email}")

        print("\n[2] Elenco wallbox...")
        try:
            evses = await client.async_get_evses()
        except DazeError as err:
            print(f"    FALLITO: {err}")
            return 1
        if not evses:
            print("    Nessuna wallbox trovata sull'account.")
            return 1
        for evse in evses:
            print(f"    - {evse.name}")
            print(f"      serial EVSE   : {evse.serial_number}")
            print(f"      serial socket : {evse.socket_serial}")
            print(f"      firmware      : {evse.firmware_version}")
            print(f"      trifase       : {evse.three_phase}")
            print(f"      fotovoltaico  : {evse.photovoltaic}")

        target = evses[0]
        print(f"\n[3] Stato di '{target.name}' (socket {target.socket_serial})...")
        try:
            state = await client.async_get_socket_state(target.socket_serial)
        except DazeError as err:
            print(f"    FALLITO: {err}")
            return 1
        print(f"    stato EVSE    : {state.evse_state}")
        print(f"    attivo        : {state.active}")
        print(f"    in pausa      : {state.is_paused}")
        print(f"    potenza (W)   : {state.power_w}")
        print(f"    energia (Wh)  : {state.energy_wh}")
        print(f"    corrente L1/L2/L3: "
              f"{state.current_l1}/{state.current_l2}/{state.current_l3}")
        print(f"    tensione L1/L2/L3: "
              f"{state.voltage_l1}/{state.voltage_l2}/{state.voltage_l3}")
        print(f"    durata (s)    : {state.charge_seconds}")
        print(f"\n    chiavi grezze remoteInfo: {sorted(state.raw)}")

        if args.test_command:
            print(f"\n[4] Comando '{args.test_command}' su {target.name}")
            confirm = input("    Confermi l'invio? (scrivi 'si'): ")
            if confirm.strip().lower() != "si":
                print("    Annullato.")
                return 0
            try:
                if args.test_command == "start":
                    await client.async_start_charge(target.socket_serial)
                elif args.test_command == "stop":
                    await client.async_stop_charge(target.socket_serial)
                else:
                    await client.async_set_max_current(
                        target.serial_number, target.socket_serial, args.amps
                    )
            except DazeError as err:
                print(f"    FALLITO: {err}")
                return 1
            print("    OK - comando accettato dall'API")

        print("\nVerifica completata.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
