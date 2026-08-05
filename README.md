# Daze Cloud (Unofficial) for Home Assistant

Unofficial Home Assistant integration for Daze wallboxes.

The integration connects to the Daze Cloud services and allows you to monitor and control your
wallbox directly from Home Assistant.

This integration is intended exclusively to allow users to access and control their own Daze
wallboxes through Home Assistant.

> [!IMPORTANT]
> This project is not affiliated with, endorsed, authorized or supported by Daze.

---

## Project Status

This is the first public release of the integration.

It has been developed and tested on real hardware, but the testing period is still limited. At
the moment it has been verified only with a Daze Home T wallbox. Compatibility with other Daze
models has not yet been confirmed.

The project should therefore be considered **Beta**.

Bug reports, testing on additional models, suggestions and pull requests are welcome.

---

## Features

- Native Home Assistant Config Flow
- HACS compatible
- Async implementation using DataUpdateCoordinator
- Start / Stop charging
- Auto Start management
- Charging power limit
- Real-time charging power
- Session energy
- Total delivered energy
- Last completed session (energy, duration, start/end)
- Per-phase voltage and current
- Grid current monitoring
- Vehicle connection detection
- Charging state
- Error detection
- Home Assistant Energy Dashboard support

---

## Supported Hardware

Currently tested with:

- ✅ Daze Home T

Other Daze models may also work but have not yet been verified.

---

## Requirements

- Home Assistant 2024.12 or newer
- A Daze account
- Internet connection

The integration communicates with Daze Cloud services and therefore requires Internet
connectivity.

---

## Installation

### HACS

1. Add this repository as a Custom Repository (Integration).
2. Install the integration.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and search for **Daze (Unofficial)**.

Log in using the same credentials used by the official Daze app.

### Manual Installation

Copy

```
custom_components/daze_cloud
```

into

```
<config>/custom_components/
```

Restart Home Assistant and add the integration.

---

## Entities

The integration exposes:

### Sensors

- Charging power
- Session energy
- Total delivered energy
- Charging duration
- Charging state
- Maximum charging current
- Grid current (L1/L2/L3)
- Charging current (L1/L2/L3)
- Grid voltage (L1/L2/L3)
- Last session energy
- Last session duration
- Last session start / end

### Binary Sensors

- Vehicle connected
- Charging in progress
- Fault status

### Controls

- Start / Stop charging
- Auto Start
- Charging power limit

---

## Charging Power

The Daze cloud accepts only a predefined set of charging power values.

The integration automatically rounds every requested value to the nearest supported level,
regardless of whether it comes from:

- the UI
- an automation
- a service call
- a script

Two entities are available:

- **Select:** choose one of the official Daze power values.
- **Number:** slider that automatically snaps to the nearest valid value.

This guarantees that Home Assistant and the Daze app always remain synchronized.

---

## Charging sessions

The Daze cloud does not expose a session history, so the integration keeps track of the **last
completed session** on its own.

A Daze "session" spans the whole period the cable stays plugged in (energy and duration are
accumulated over that period), and it ends when the cable is unplugged. When that happens the
integration records the session as the *last session* and exposes its energy, duration, start
and end.

Notes:

- The last-session sensors stay empty until the **first session ends** (cable unplugged); an open
  session is not shown as "last" yet.
- Sessions that only contain the connection handshake (no energy delivered) are ignored, so they
  do not overwrite the last real session.
- The last session is persisted, so it survives Home Assistant restarts.
- For long-term statistics (per day/week/month) use the Home Assistant recorder / Energy
  Dashboard on the **Total delivered energy** sensor.

---

## Notes

- The integration depends on Daze Cloud availability.
- If Daze services are unavailable, the integration cannot communicate with the wallbox.
- Polling intervals that are too short are discouraged because the integration communicates with
  third-party cloud services.

---

## Testing

A small command-line utility is included in the repository:

```bash
python3 tools/test_daze_api.py --username your@email.com
```

It allows you to:

- authenticate
- list available wallboxes
- read wallbox status
- optionally test charging commands after confirmation

---

## Disclaimer

This software is distributed free of charge and without any warranty, to the maximum extent
permitted by applicable law.

Before using this integration, ensure that you understand its operation and verify that it is
suitable for your installation.

The author provides no guarantee regarding reliability, availability, future compatibility with
Daze Cloud services or suitability for any particular purpose.

To the maximum extent permitted by applicable law, the author disclaims any liability for
damages, losses or malfunctions arising from the installation or use of this software.

---

## Trademark Notice

"Daze" and "Dazebox" are trademarks of their respective owners.

This project is independent and is not affiliated with, endorsed, authorized or supported by
Daze.

---

## License

This project is released under the GNU General Public License v3.0 (GPL-3.0).

See the [LICENSE](LICENSE) file for details.
