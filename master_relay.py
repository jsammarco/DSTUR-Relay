#!/usr/bin/env python3
"""
universal_relay.py — merged USB Relay CLI.

Goal:
- Work regardless of which of the two common CH340 relay firmwares is present.
- "all on/off" sends BOTH:
    (A) broadcast: A0 0F <01|00> <chk>  (some boards support this)
    (B) loop CH1..N: A0 <ch> <01|00> <chk> (most compatible)
- "relay <n> on/off" sends per-channel A0 <n> <01|00> <chk>
- "status" tries BOTH query variants:
    query-by-02: A0 <ch|0F> 02 <chk>
    query-by-FF: A0 <ch>    FF <chk>
  and decodes ASCII responses like: b"CH1:OFFCH2:ON...".

Usage examples:
  python3 universal_relay.py list-ports
  python3 universal_relay.py --port /dev/ttyUSB0 all on
  python3 universal_relay.py --port /dev/ttyUSB0 all off
  python3 universal_relay.py --port /dev/ttyUSB0 relay 3 on
  python3 universal_relay.py --port /dev/ttyUSB0 relay 3 off
  python3 universal_relay.py --port /dev/ttyUSB0 status all
  python3 universal_relay.py --port /dev/ttyUSB0 status 2 --raw
"""

import argparse
import csv
import json
import re
import time
from io import StringIO

import serial
from serial.tools import list_ports


DEFAULT_BAUD_RATE = 9600


# ----------------------------
# Protocol helpers
# ----------------------------
def build_command_bytes(b1: int, b2: int, b3: int) -> bytes:
    checksum = (b1 + b2 + b3) & 0xFF
    return bytes([b1 & 0xFF, b2 & 0xFF, b3 & 0xFF, checksum])


def cmd_relay_on(relay_num: int) -> bytes:
    return build_command_bytes(0xA0, relay_num, 0x01)


def cmd_relay_off(relay_num: int) -> bytes:
    return build_command_bytes(0xA0, relay_num, 0x00)


def cmd_all_on_broadcast() -> bytes:
    # relay.py-style "all on"
    return build_command_bytes(0xA0, 0x0F, 0x01)


def cmd_all_off_broadcast() -> bytes:
    # relay.py-style "all off"
    return build_command_bytes(0xA0, 0x0F, 0x00)


def cmd_query_02(target: int) -> bytes:
    # relay.py-style query uses 0x02 (target can be 0x0F for "all" or 1..N)
    return build_command_bytes(0xA0, target, 0x02)


def cmd_query_ff(relay_num: int) -> bytes:
    # LCUS_relay.py-style query uses 0xFF per relay (many boards return nothing)
    return build_command_bytes(0xA0, relay_num, 0xFF)


# ----------------------------
# Serial port listing helpers
# ----------------------------
def _format_port_value(value, empty_placeholder="-"):
    if value is None:
        return empty_placeholder
    value = str(value).strip()
    return value if value else empty_placeholder


def _format_vid_pid(value):
    if value is None:
        return "-"
    return "0x{0:04X}".format(value)


def port_to_dict(port):
    model = port.product or port.description
    address = getattr(port, "location", None) or getattr(port, "interface", None)
    return {
        "port": port.device,
        "vid": port.vid,
        "pid": port.pid,
        "manufacturer": port.manufacturer,
        "model": model,
        "hwid": port.hwid,
        "address": address,
    }


def format_ports_table(ports):
    headers = ["Port", "VID", "PID", "Manufacturer", "Model", "HWID", "Address"]
    rows = []
    for port in ports:
        model = port.product or port.description
        address = getattr(port, "location", None) or getattr(port, "interface", None)
        rows.append(
            [
                _format_port_value(port.device),
                _format_vid_pid(port.vid),
                _format_vid_pid(port.pid),
                _format_port_value(port.manufacturer),
                _format_port_value(model),
                _format_port_value(port.hwid),
                _format_port_value(address),
            ]
        )

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    lines = []
    lines.append(" | ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    lines.append("-+-".join("-" * w for w in widths))
    for row in rows:
        lines.append(" | ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(lines)


def format_ports_csv(ports):
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["port", "vid", "pid", "manufacturer", "model", "hwid", "address"],
    )
    writer.writeheader()
    for port in ports:
        writer.writerow(port_to_dict(port))
    return output.getvalue().strip()


def format_ports_json(ports):
    payload = [port_to_dict(port) for port in ports]
    return json.dumps(payload, indent=2)


def resolve_com_port(requested_port):
    if requested_port:
        return requested_port
    ports = [p.device for p in list_ports.comports()]
    if not ports:
        raise RuntimeError("No serial ports found on this system.")
    return ports[0]


# ----------------------------
# Serial transact
# ----------------------------
def _transact(
    com_port,
    baud_rate,
    payload,
    timeout,
    read_response=False,
    settle=0.05,
    debug=False,
):
    try:
        with serial.Serial(
            port=com_port,
            baudrate=baud_rate,
            timeout=timeout,
            write_timeout=timeout,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        ) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            if debug:
                print("TX:", payload.hex(" "))

            ser.write(payload)
            ser.flush()

            if not read_response:
                return b""

            time.sleep(settle)
            n = ser.in_waiting
            resp = ser.read(n if n else 128)

            if debug:
                if resp:
                    print("RX:", resp.hex(" "))
                else:
                    print("RX: (no data)")

            return resp

    except (serial.SerialException, OSError) as e:
        raise RuntimeError(
            "Could not open serial port '{0}'. "
            "Run `python3 universal_relay.py list-ports` to see valid ports. "
            "Original error: {1}".format(com_port, e)
        ) from None


# ----------------------------
# Actions
# ----------------------------
def _sleep_small():
    time.sleep(0.01)


def send_one_relay(com_port, baud, relay_num, state, timeout, debug=False):
    if relay_num < 1 or relay_num > 32:
        raise RuntimeError("Relay number must be 1..32; got {0}".format(relay_num))

    if state == "on":
        payload = cmd_relay_on(relay_num)
    elif state == "off":
        payload = cmd_relay_off(relay_num)
    else:
        raise RuntimeError("Invalid relay state: {0}".format(state))

    _transact(com_port, baud, payload, timeout, read_response=False, debug=debug)


def pulse_one_relay(com_port, baud, relay_num, seconds, timeout, debug=False):
    send_one_relay(com_port, baud, relay_num, "on", timeout, debug=debug)
    time.sleep(seconds)
    send_one_relay(com_port, baud, relay_num, "off", timeout, debug=debug)


def send_all_relays_universal(com_port, baud, channels, state, timeout, debug=False):
    """
    UNIVERSAL "all":
    1) Send broadcast (A0 0F ...) for boards that support it
    2) Also loop CH1..CHN for boards that *don't* support broadcast
    """
    if state not in ("on", "off"):
        raise RuntimeError("Invalid all state: {0}".format(state))
    if channels < 1 or channels > 32:
        raise RuntimeError("--channels must be 1..32; got {0}".format(channels))

    # (1) broadcast attempt
    if state == "on":
        _transact(com_port, baud, cmd_all_on_broadcast(), timeout, read_response=False, debug=debug)
    else:
        _transact(com_port, baud, cmd_all_off_broadcast(), timeout, read_response=False, debug=debug)

    _sleep_small()

    # (2) per-channel loop (most compatible)
    for relay_num in range(1, channels + 1):
        if state == "on":
            payload = cmd_relay_on(relay_num)
        else:
            payload = cmd_relay_off(relay_num)
        _transact(com_port, baud, payload, timeout, read_response=False, debug=debug)
        _sleep_small()


def pulse_all_relays_universal(com_port, baud, channels, seconds, timeout, debug=False):
    send_all_relays_universal(com_port, baud, channels, "on", timeout, debug=debug)
    time.sleep(seconds)
    send_all_relays_universal(com_port, baud, channels, "off", timeout, debug=debug)


def decode_status_ascii(response: bytes):
    """
    Typical response:
      b"CH1:OFFCH2:ON...CH8:OFF"
    """
    text = response.decode("ascii", errors="ignore")
    matches = re.findall(r"CH(\d+):(ON|OFF)", text)
    if not matches:
        return None
    return {"ch{0}".format(ch): (1 if v == "ON" else 0) for ch, v in matches}


def query_status_universal(com_port, baud, target, channels, timeout, debug=False):
    """
    Try both query variants and return (decoded_dict_or_None, raw_bytes).
    - If target == "all": try A0 0F 02 chk first, then per-channel FF/02 as fallback.
    - If target is int: try A0 <n> 02 chk then A0 <n> FF chk.
    """
    if target == "all":
        # First try "all" query with 0x02 (relay.py style)
        resp = _transact(com_port, baud, cmd_query_02(0x0F), timeout, read_response=True, debug=debug)
        decoded = decode_status_ascii(resp) if resp else None
        if decoded:
            return decoded, resp

        # Fallback: probe channels; accept the first decodable response and keep going best-effort
        decoded_all = {}
        raw_last = b""
        for i in range(1, channels + 1):
            # Try 0x02 then 0xFF for each channel
            resp_i = _transact(com_port, baud, cmd_query_02(i), timeout, read_response=True, debug=debug)
            raw_last = resp_i or raw_last
            d = decode_status_ascii(resp_i) if resp_i else None
            if d and f"ch{i}" in d:
                decoded_all[f"ch{i}"] = d[f"ch{i}"]
                _sleep_small()
                continue

            resp_i = _transact(com_port, baud, cmd_query_ff(i), timeout, read_response=True, debug=debug)
            raw_last = resp_i or raw_last
            d = decode_status_ascii(resp_i) if resp_i else None
            if d and f"ch{i}" in d:
                decoded_all[f"ch{i}"] = d[f"ch{i}"]
            else:
                decoded_all[f"ch{i}"] = None
            _sleep_small()

        return (decoded_all if decoded_all else None), raw_last

    # target is a specific relay number
    relay_num = int(target)
    if relay_num < 1 or relay_num > 32:
        raise RuntimeError("Relay number must be 1..32; got {0}".format(relay_num))

    # Try 0x02 query first
    resp = _transact(com_port, baud, cmd_query_02(relay_num), timeout, read_response=True, debug=debug)
    decoded = decode_status_ascii(resp) if resp else None
    if decoded and f"ch{relay_num}" in decoded:
        return decoded, resp

    # Try 0xFF query
    resp = _transact(com_port, baud, cmd_query_ff(relay_num), timeout, read_response=True, debug=debug)
    decoded = decode_status_ascii(resp) if resp else None
    return decoded, resp


def parse_hex_bytes(tokens):
    if not tokens:
        raise RuntimeError("No hex bytes provided.")
    raw = " ".join(tokens).replace(",", " ").strip()
    if not raw:
        raise RuntimeError("No hex bytes provided.")
    chunks = [c for c in raw.split() if c]
    data = []
    for chunk in chunks:
        if chunk.lower().startswith("0x"):
            chunk = chunk[2:]
        if not re.fullmatch(r"[0-9a-fA-F]+", chunk):
            raise RuntimeError("Invalid hex byte sequence: '{0}'".format(chunk))
        if len(chunk) % 2 != 0:
            raise RuntimeError("Hex byte sequence must have even length: '{0}'".format(chunk))
        for i in range(0, len(chunk), 2):
            data.append(int(chunk[i : i + 2], 16))
    if not data:
        raise RuntimeError("No hex bytes provided.")
    return bytes(data)


# ----------------------------
# CLI
# ----------------------------
def parse_args(argv=None):
    # Global options live here. We keep this parser so we can still accept globals
    # in the first stage, but we ALSO attach it as a parent to the main parser so
    # `-h/--help` shows them.
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument(
        "-p",
        "--port",
        help=(
            "Serial port device to use (e.g. COM8 on Windows, /dev/ttyUSB0 on Linux). "
            "If omitted, the first detected port is used (often COM1 on Windows). "
            "Use `list-ports` to see available ports."
        ),
    )
    global_parser.add_argument(
        "--baud",
        type=int,
        default=DEFAULT_BAUD_RATE,
        help=f"Baud rate (default {DEFAULT_BAUD_RATE})",
    )
    global_parser.add_argument(
        "--timeout",
        type=float,
        default=1.0,
        help="Serial read/write timeout in seconds (default 1.0)",
    )
    global_parser.add_argument(
        "--channels",
        type=int,
        default=8,
        help="How many relays the board has (default 8; supports up to 32)",
    )
    global_parser.add_argument(
        "--debug",
        action="store_true",
        help="Print TX/RX bytes (hex) for troubleshooting",
    )

    # Stage 1: parse global flags wherever they appear (pre-subcommand, typically).
    globals_ns, remaining = global_parser.parse_known_args(argv)

    epilog = """\
Examples (Windows):
  python universal_relay.py list-ports
  python universal_relay.py -p COM8 all on
  python universal_relay.py -p COM8 all off
  python universal_relay.py -p COM8 relay 3 on
  python universal_relay.py -p COM8 relay 3 pulse --seconds 1.5
  python universal_relay.py -p COM8 status all
  python universal_relay.py -p COM8 status 2 --raw

Examples (Linux/macOS):
  python3 universal_relay.py list-ports --detailed
  python3 universal_relay.py --port /dev/ttyUSB0 all on
  python3 universal_relay.py --port /dev/ttyUSB0 status all --raw

Notes:
- If --port is omitted, the program uses the first detected serial port. On Windows this
  is often COM1, which may NOT be your relay. Run `list-ports --detailed` to confirm.
- `all on/off` sends BOTH a broadcast command and then loops channel 1..N for maximum
  compatibility across common CH340 relay firmwares.
- `status` is device-dependent; some boards return ASCII like: CH1:OFFCH2:ON...
"""

    # Stage 2: build the real CLI, inheriting global options so help shows them.
    parser = argparse.ArgumentParser(
        description="Universal USB Relay CLI (merged protocols)",
        parents=[global_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p_list = sub.add_parser(
        "list-ports",
        help="List detected serial ports (use this to find COM8, /dev/ttyUSB0, etc.)",
    )
    list_group = p_list.add_mutually_exclusive_group()
    list_group.add_argument("--detailed", action="store_true", help="Show detailed port info in a table")
    list_group.add_argument("--csv", action="store_true", help="Output ports as CSV")
    list_group.add_argument("--json", action="store_true", help="Output ports as JSON")

    p_all = sub.add_parser(
        "all",
        help="Control all relays at once (broadcast + per-channel loop)",
    )
    p_all.add_argument("state", choices=["on", "off", "pulse"], help="Desired state for all relays")
    p_all.add_argument("--seconds", type=float, default=3.0, help="Pulse duration in seconds (default 3.0)")

    p_relay = sub.add_parser(
        "relay",
        help="Control a single relay channel",
    )
    p_relay.add_argument("number", type=int, help="Relay number (1..N)")
    p_relay.add_argument("state", choices=["on", "off", "pulse"], help="Desired relay state")
    p_relay.add_argument("--seconds", type=float, default=1.0, help="Pulse duration in seconds (default 1.0)")

    p_status = sub.add_parser(
        "status",
        help="Query relay status (tries multiple query variants; device-dependent)",
    )
    p_status.add_argument(
        "target",
        help="'all' or relay number",
        choices=["all"] + [str(i) for i in range(1, 33)],
    )
    p_status.add_argument("--raw", action="store_true", help="Print raw response hex too")

    p_raw = sub.add_parser(
        "raw",
        help="Send raw hex bytes and read a response (advanced troubleshooting)",
    )
    p_raw.add_argument("bytes", nargs="+", help="Hex bytes to send (e.g. A0 01 01 A2)")
    p_raw.add_argument("--raw", action="store_true", help="Print response as raw hex bytes")

    cmd_ns = parser.parse_args(remaining)
    return globals_ns, cmd_ns


def main(argv=None):
    try:
        g, c = parse_args(argv)

        if c.command == "list-ports":
            ports = list_ports.comports()
            if c.csv:
                print(format_ports_csv(ports))
            elif c.json:
                print(format_ports_json(ports))
            elif c.detailed:
                print(format_ports_table(ports))
            else:
                for p in ports:
                    print(p.device)
            return 0

        com_port = resolve_com_port(g.port)

        if c.command == "relay":
            relay_num = int(c.number)
            if c.state in ("on", "off"):
                send_one_relay(com_port, g.baud, relay_num, c.state, g.timeout, debug=g.debug)
                print(f"OK: relay{relay_num} {c.state} ({com_port})")
                return 0

            pulse_one_relay(com_port, g.baud, relay_num, c.seconds, g.timeout, debug=g.debug)
            print(f"OK: relay{relay_num} pulse {c.seconds}s ({com_port})")
            return 0

        if c.command == "all":
            if c.state in ("on", "off"):
                send_all_relays_universal(com_port, g.baud, g.channels, c.state, g.timeout, debug=g.debug)
                print(f"OK: all relays {c.state} (broadcast + loop 1..{g.channels}) ({com_port})")
                return 0

            pulse_all_relays_universal(com_port, g.baud, g.channels, c.seconds, g.timeout, debug=g.debug)
            print(f"OK: all relays pulse {c.seconds}s (broadcast + loop 1..{g.channels}) ({com_port})")
            return 0

        if c.command == "status":
            decoded, resp = query_status_universal(
                com_port,
                g.baud,
                c.target,
                g.channels,
                g.timeout,
                debug=g.debug,
            )

            if c.raw:
                print("RAW:", resp.hex() if resp else "(no data)")

            if not decoded:
                print("Unable to decode status")
                return 1

            if c.target == "all":
                statuses = []
                for relay_num in range(1, g.channels + 1):
                    v = decoded.get(f"ch{relay_num}")
                    statuses.append(f"relay{relay_num}={v if v is not None else '?'}")
                print(" ".join(statuses))
                return 0

            relay_num = int(c.target)
            v = decoded.get(f"ch{relay_num}") if decoded else None
            print(f"relay{relay_num}={v if v is not None else '?'}")
            return 0

        if c.command == "raw":
            payload = parse_hex_bytes(c.bytes)
            resp = _transact(com_port, g.baud, payload, g.timeout, read_response=True, debug=g.debug)
            if c.raw:
                print("RAW:", resp.hex())
            else:
                if resp:
                    print(resp.decode("ascii", errors="replace"))
                else:
                    print("No response")
            return 0

        return 2

    except RuntimeError as e:
        print("ERROR:", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
