#!/usr/bin/env python3
"""
LCUS_relay.py — USB Relay CLI for CH340 boards using 4-byte frames:

Frame:  [0xA0, channel, value, checksum]
checksum = (byte1 + byte2 + byte3) & 0xFF

Per your product note examples:
  Relay1 ON:  A0 01 01 A2
  Relay1 OFF: A0 01 00 A1
  Relay2 ON:  A0 02 01 A3
  Relay2 OFF: A0 02 00 A2
  FF = query  (device-dependent response; many boards return nothing)

Important:
- Many of these boards DO NOT support a broadcast "all" command.
  This script implements "all on/off/pulse" by looping channels 1..N.

Usage examples:
  python LCUS_relay.py list-ports
  python LCUS_relay.py --port COM12 relay 1 on
  python LCUS_relay.py --port COM12 relay 2 pulse --seconds 0.5
  python LCUS_relay.py --port COM12 all off
  python LCUS_relay.py --port COM12 --channels 2 all off
  python LCUS_relay.py --port COM12 raw A0 01 01 A2
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


def cmd_relay(relay_num: int, state: str) -> bytes:
    if state == "on":
        return build_command_bytes(0xA0, relay_num, 0x01)
    if state == "off":
        return build_command_bytes(0xA0, relay_num, 0x00)
    if state == "query":
        return build_command_bytes(0xA0, relay_num, 0xFF)
    raise ValueError("Unknown state: {}".format(state))


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
def _transact(com_port, baud_rate, payload, timeout, read_response=False, settle=0.05, debug=False):
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
            "Run `python LCUS_relay.py list-ports` to see valid ports. "
            "Original error: {1}".format(com_port, e)
        ) from None


# ----------------------------
# Actions
# ----------------------------
def send_one_relay(com_port, baud, relay_num, state, timeout, debug=False):
    if relay_num < 1 or relay_num > 32:
        raise RuntimeError("Relay number must be >=1; got {0}".format(relay_num))
    payload = cmd_relay(relay_num, state)
    _transact(com_port, baud, payload, timeout, read_response=False, debug=debug)


def pulse_one_relay(com_port, baud, relay_num, seconds, timeout, debug=False):
    send_one_relay(com_port, baud, relay_num, "on", timeout, debug=debug)
    time.sleep(seconds)
    send_one_relay(com_port, baud, relay_num, "off", timeout, debug=debug)


def send_all_relays(com_port, baud, channels, state, timeout, debug=False):
    """
    Loop CH1..CHN. This is the most compatible way with these boards.
    """
    for relay_num in range(1, channels + 1):
        payload = cmd_relay(relay_num, state)
        _transact(com_port, baud, payload, timeout, read_response=False, debug=debug)
        time.sleep(0.01)


def pulse_all_relays(com_port, baud, channels, seconds, timeout, debug=False):
    send_all_relays(com_port, baud, channels, "on", timeout, debug=debug)
    time.sleep(seconds)
    send_all_relays(com_port, baud, channels, "off", timeout, debug=debug)


def decode_status_ascii(response):
    """
    Some boards return ASCII like: b"CH1:OFFCH2:ON...CH8:OFF"
    """
    text = response.decode("ascii", errors="ignore")
    matches = re.findall(r"CH(\d+):(ON|OFF)", text)
    if not matches:
        return None
    return {"ch{0}".format(ch): (1 if v == "ON" else 0) for ch, v in matches}


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
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument("--port")
    global_parser.add_argument("--baud", type=int, default=DEFAULT_BAUD_RATE)
    global_parser.add_argument("--timeout", type=float, default=1.0)
    global_parser.add_argument("--channels", type=int, default=8, help="How many relays the board has (default 8)")
    global_parser.add_argument("--debug", action="store_true", help="Print TX/RX bytes")

    globals_ns, remaining = global_parser.parse_known_args(argv)

    parser = argparse.ArgumentParser(description="USB Relay CLI (A0 ch val checksum)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-ports")
    list_group = p_list.add_mutually_exclusive_group()
    list_group.add_argument("--detailed", action="store_true", help="Show detailed port info")
    list_group.add_argument("--csv", action="store_true", help="Output ports as CSV")
    list_group.add_argument("--json", action="store_true", help="Output ports as JSON")

    p_all = sub.add_parser("all")
    p_all.add_argument("state", choices=["on", "off", "pulse"])
    p_all.add_argument("--seconds", type=float, default=3.0)

    p_relay = sub.add_parser("relay", help="Control a single relay")
    p_relay.add_argument("number", type=int, help="Relay number (1..N)")
    p_relay.add_argument("state", choices=["on", "off", "pulse"])
    p_relay.add_argument("--seconds", type=float, default=1.0)

    p_status = sub.add_parser("status", help="Query relay status (device-dependent)")
    p_status.add_argument("target", choices=[str(i) for i in range(1, 33)] + ["all"])
    p_status.add_argument("--raw", action="store_true", help="Print raw response hex")

    p_raw = sub.add_parser("raw", help="Send raw hex bytes and read response")
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
                print("OK: relay{0} {1} ({2})".format(relay_num, c.state, com_port))
                return 0

            pulse_one_relay(com_port, g.baud, relay_num, c.seconds, g.timeout, debug=g.debug)
            print("OK: relay{0} pulse {1}s ({2})".format(relay_num, c.seconds, com_port))
            return 0

        if c.command == "all":
            if c.state in ("on", "off"):
                send_all_relays(com_port, g.baud, g.channels, c.state, g.timeout, debug=g.debug)
                print("OK: all relays {0} (loop 1..{1}) ({2})".format(c.state, g.channels, com_port))
                return 0

            pulse_all_relays(com_port, g.baud, g.channels, c.seconds, g.timeout, debug=g.debug)
            print("OK: all relays pulse {0}s (loop 1..{1}) ({2})".format(c.seconds, g.channels, com_port))
            return 0

        if c.command == "status":
            # Query uses 0xFF as noted; many boards return nothing.
            if c.target == "all":
                # Query each channel
                results = []
                for i in range(1, g.channels + 1):
                    resp = _transact(
                        com_port,
                        g.baud,
                        cmd_relay(i, "query"),
                        g.timeout,
                        read_response=True,
                        debug=g.debug,
                    )
                    if c.raw:
                        results.append(f"r{i}:{resp.hex()}")
                    else:
                        decoded = decode_status_ascii(resp)
                        if decoded and f"ch{i}" in decoded:
                            results.append(f"relay{i}={decoded[f'ch{i}']}")
                        else:
                            results.append(f"relay{i}=?")
                    time.sleep(0.03)
                print(" ".join(results))
                return 0

            relay_num = int(c.target)
            resp = _transact(
                com_port,
                g.baud,
                cmd_relay(relay_num, "query"),
                g.timeout,
                read_response=True,
                debug=g.debug,
            )
            if c.raw:
                print("RAW:", resp.hex())
                return 0

            decoded = decode_status_ascii(resp)
            if decoded and f"ch{relay_num}" in decoded:
                print("relay{0}={1}".format(relay_num, decoded[f"ch{relay_num}"]))
                return 0

            print("Unable to decode status. Raw hex:", resp.hex())
            return 1

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
