# Raspberry Pi 4B Laser Measurement Replacement

This project replaces the ESP32P4 + Siemens S7-1200 + wenglor IO-Link master stack with a Raspberry Pi 4B talking to an ifm AL1322 IO-Link master. It preserves the legacy measurement logic and HMI ASCII command protocol from `laser_measure_inch_working_v2.ino`.

## Features
- AL1322 IO-Link REST client (GET preferred, POST available)
- Legacy measurement logic replicated in mm internally, inches displayed
- Nextion-style HMI serial protocol with 0xFF 0xFF 0xFF framing
- FastAPI dashboard for status, diagnostics, logs, operator view, targets, and WiFi setup
- Config persistence in JSON
- MOCK_AL1322 mode for deterministic testing without hardware

## File Layout
- `src/main.py` main entrypoint (measurement loop + HMI + web server)
- `src/webapp.py` FastAPI app
- `src/al1322_client.py` AL1322 REST client + mock client
- `src/measurement.py` measurement logic (legacy-compatible)
- `src/hmi.py` Nextion-style serial handler
- `src/config_store.py` config persistence and HMI/web updates
- `templates/` dashboard HTML
- `static/` dashboard CSS
- `config/config.json` defaults
- `systemd/laser_measure.service` systemd unit

## Runtime Behaviour
- Polls AL1322 at 2 Hz (0.5 s), using GET `/iolinkmaster/port[n]/iolinkdevice/pdin/getdata`
- Decodes hex strings into engineering units
- Applies offsets, computes d1, d2, dDelta, end1_rng, end2_rng, length
- PASS/FAIL logic matches legacy sketch
- Updates HMI and dashboard after each cycle
- Operator page designed for 1024x600 touchscreen at `/operator`
- Targets/offsets page at `/targets`, WiFi setup at `/wifi`

## Configuration
All values are stored internally in mm. HMI and dashboard inputs/outputs use inches.

`config/config.json`:
- `al1322_ip`: IP address of AL1322
- `poll_interval_s`: polling interval (seconds)
- `request_timeout_s`: REST timeout per call
- `targets`: d1/d2/length targets and tolerances (mm)
- `offsets_mm`: per-channel offsets (mm)
- `channels`: per-channel conversion
  - `raw_format`: `uint_be` | `uint_le` | `int_be` | `int_le` | `float_be` | `float_le`
  - `scale`: multiplier applied to decoded value (default 0.001 for um -> mm)
  - `offset`: extra offset in mm
  - `start_bit`, `bit_length`: optional packed bit slicing (integer formats only)

### Environment Overrides
- `CONFIG_PATH` path to config file (default `config/config.json`)
- `AL1322_IP` override IP
- `HMI_SERIAL_PORT` override serial port (use `DISABLED` to disable HMI)
- `HMI_BAUD` override baud rate
- `MOCK_AL1322=1` use mock data (dashboard + HMI still run)

## HMI Protocol (Legacy-Compatible)
UART frames are ASCII terminated by `0xFF 0xFF 0xFF`.

Commands received:
- `SET <key> <value_in_inches>` (keys: `d1t`, `d1tol`, `d2t`, `d2tol`, `lent`, `lentol`, `ddelmax`, `e1max`, `e2max`, `off0`..`off7`)
- `SAVE`
- `REQ TARGETS`
- `REQ OFFSETS`
- `DUMP`

Commands sent (examples):
- `tD1.txt="1.234"` + terminator
- `tStatus.txt="PASS"` + terminator
- `tD1Target.txt="1.234"` + terminator

## Systemd
Copy `systemd/laser_measure.service` to `/etc/systemd/system/`, edit `WorkingDirectory` and `ExecStart` as needed, then:

```sh
sudo systemctl daemon-reload
sudo systemctl enable laser_measure.service
sudo systemctl start laser_measure.service
```

## Assumptions
- Legacy behaviour is based on `/Users/ceri/Documents/laser_measure_inch_working_v2.ino` because `/mnt/data/laser_measure_inch_working_v2.ino` was not available.
- Default AL1322 IP is set to `192.168.100.1`; update this to match your network.
- HMI serial port default is `/dev/serial0` at 115200 baud.
- `CONSTANT_LENGTH_MM = 1165.0` matches the legacy sketch.
- Mock mode returns deterministic values (0/5/10 um pattern) for all 8 ports.
