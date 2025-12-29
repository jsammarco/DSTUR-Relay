# DSTUR-Relay

Cross-platform command line relay control tool for managing USB relay boards (tested with DSTUR-T20; also expected to work with DSTUR-T10 and up to 8-relay boards).

![DSTUR Relay GUI](https://raw.githubusercontent.com/jsammarco/DSTUR-Relay/b4a5ef59c3c3e2f96ef43d8d93da4588ab9a1b45/gui/Screenshot%202025-12-29%20115413.jpg)

![DSTUR USB relay board](https://wiki.diustou.com/en/w/upload/0/08/USB_Relay_%28TC%2C_8%2C_Opto%29_%E4%BA%A7%E5%93%811.png)

## What's included

- **CLI (`relay.py`)**: Python-based command line tool for listing ports, toggling relays, and querying status.
- **GUI (`gui/dstur-relay-gui`)**: Desktop UI built with **Tauri + HTML/CSS/JavaScript** that shells out to the `relay.exe` binary produced from the CLI.

## Requirements

- Python 3.8+ (for the CLI)
- `pyserial` (see `requirements.txt`)
- For GUI development: Node.js + npm, Rust toolchain, and the Tauri CLI

## Installation

```bash
python -m pip install -r requirements.txt
```

## Usage

`relay.py` is a CLI that can list available serial ports, toggle both relays together, control an individual relay, pulse relays for a set duration, and query relay status.

```bash
python relay.py [--port PORT] [--baud BAUD] [--timeout SECONDS] <command> [command options]
```

### Global options

- `--port PORT` – Specific serial/COM port to use (e.g., `COM3`, `/dev/ttyUSB0`). If omitted, the first available port is used.
- `--baud BAUD` – Baud rate (default: `9600`).
- `--timeout SECONDS` – Read/write timeout in seconds (default: `1.0`).

### Commands

| Command | Arguments | Description |
| --- | --- | --- |
| `list-ports` | `--detailed`, `--csv`, `--json` (optional) | List all detected serial ports. Use `--detailed` for a table, `--csv` for CSV, or `--json` for JSON output. Detailed and structured outputs include VID/PID, manufacturer, model, HWID, and address. |
| `all` | `state` (`on` \| `off` \| `pulse`), `--seconds` (optional; default `3.0`) | Control both relays together: turn on, turn off, or pulse for the specified number of seconds. When using `pulse`, `--seconds` defines how long the relays stay on before automatically turning off. |
| `relay` | `number` (`1` \| `2` \| `3` \| `4` \| `5` \| `6` \| `7` \| `8`), `state` (`on` \| `off` \| `pulse`), `--seconds` (optional; default `1.0`) | Control a single relay: turn on, turn off, or pulse for the specified number of seconds. `--seconds` only applies to `pulse`. |
| `status` | `target` (`1` \| `2` \| `3` \| `4` \| `5` \| `6` \| `7` \| `8` \| `all`), `--raw` (optional) | Query status for a single relay or all relays. `--raw` prints the raw hex response before decoding. |
| `raw` | `bytes` (hex byte sequence), `--raw` (optional) | Send raw hex bytes and read a response. By default, prints any ASCII response; use `--raw` to print the raw hex response. |

### Examples

List available ports:

```bash
python relay.py list-ports
```

List ports with detailed USB info:

```bash
python relay.py list-ports --detailed
```

List ports as CSV:

```bash
python relay.py list-ports --csv
```

List ports as JSON:

```bash
python relay.py list-ports --json
```

Turn on both relays using the first detected port:

```bash
python relay.py all on
```

Pulse both relays for 5 seconds on a specific port:

```bash
python relay.py --port COM3 all pulse --seconds 5
```

Turn on relay 1 only:

```bash
python relay.py relay 1 on
```

Pulse relay 2 for 2 seconds:

```bash
python relay.py relay 2 pulse --seconds 2
```

Turn on relays 3–8 one-by-one:

```bash
for relay in 3 4 5 6 7 8; do
  python relay.py relay "$relay" on
done
```

Check status for both relays (decoded output) on a custom baud rate:

```bash
python relay.py --baud 9600 status all
```

Show raw status response for relay 1:

```bash
python relay.py status 1 --raw
```

Send a raw hex command and print the ASCII response:

```bash
python relay.py raw A0 01 00 A1
```

Send a raw hex command and print the raw response bytes:

```bash
python relay.py raw A0 0F 02 A1 --raw
```

## GUI

The GUI lives in `gui/dstur-relay-gui` and is implemented with Tauri (Rust backend) and a vanilla HTML/CSS/JS front end. The Tauri backend invokes `relay.exe` (built-in the exe) to execute the same relay commands exposed by the CLI.

### Running the GUI (dev)

```bash
cd gui/dstur-relay-gui
npm install
npm run tauri dev
```

### Packaging notes

- The Tauri app contains relay.exe in it's resources so only the gui exe is needed
- When building with tauri, it expects `relay.exe` alongside the app binary or in `src-tauri/bin/relay.exe`.
- `relay.exe` is included in the repo (root) and mirrored in `gui/dstur-relay-gui/src-tauri/bin/`.

## Device compatibility

- Designed and tested with the DSTUR-T20 two-relay USB board.
- Expected to work with the DSTUR-T10 single-relay board (commands that target relay 2 will have no effect).
- Supports up to 8-relay boards (relays beyond the hardware count will have no effect).
