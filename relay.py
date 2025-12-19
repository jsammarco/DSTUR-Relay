import argparse
import csv
import json
import re
import time
from io import StringIO

import serial
from serial.tools import list_ports


DEFAULT_BAUD_RATE = 9600
RELAY_CHOICES = [str(i) for i in range(1, 9)]

CMD = {
    "relay": {},
    "all": {},
    "query": {},
}


def build_command_bytes(data1, data2, data3):
    checksum = (data1 + data2 + data3) & 0xFF
    return bytes([data1, data2, data3, checksum])


CMD["relay"] = {
    relay_num: {
        "on": build_command_bytes(0xA0, relay_num, 0x01),
        "off": build_command_bytes(0xA0, relay_num, 0x00),
    }
    for relay_num in range(1, 9)
}
CMD["all"] = {
    "on": build_command_bytes(0xA0, 0x0F, 0x01),
    "off": build_command_bytes(0xA0, 0x0F, 0x00),
}
CMD["query"] = {
    "all": build_command_bytes(0xA0, 0x0F, 0x02),
}
CMD["query"].update({relay_num: build_command_bytes(0xA0, relay_num, 0x02) for relay_num in range(1, 9)})


def get_available_serial_ports():
    return [p.device for p in list_ports.comports()]


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
    parent = getattr(port, "usb_device_path", None) or getattr(port, "parent", None)
    address = getattr(port, "location", None) or getattr(port, "interface", None)
    return {
        "port": port.device,
        "vid": port.vid,
        "pid": port.pid,
        "manufacturer": port.manufacturer,
        "model": model,
        "hwid": port.hwid,
        "parent": parent,
        "address": address,
    }


def format_ports_table(ports):
    headers = ["Port", "VID", "PID", "Manufacturer", "Model", "HWID", "Parent", "Address"]
    rows = []
    for port in ports:
        model = port.product or port.description
        parent = getattr(port, "usb_device_path", None) or getattr(port, "parent", None)
        address = getattr(port, "location", None) or getattr(port, "interface", None)
        rows.append(
            [
                _format_port_value(port.device),
                _format_vid_pid(port.vid),
                _format_vid_pid(port.pid),
                _format_port_value(port.manufacturer),
                _format_port_value(model),
                _format_port_value(port.hwid),
                _format_port_value(parent),
                _format_port_value(address),
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    lines = []
    header_line = " | ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers))
    divider = "-+-".join("-" * width for width in widths)
    lines.append(header_line)
    lines.append(divider)
    for row in rows:
        lines.append(" | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row)))
    return "\n".join(lines)


def format_ports_csv(ports):
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["port", "vid", "pid", "manufacturer", "model", "hwid", "parent", "address"],
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
    ports = get_available_serial_ports()
    if not ports:
        raise RuntimeError("No serial ports found on this system.")
    return ports[0]


def _transact(com_port, baud_rate, payload, timeout, read_response=False):
    try:
        with serial.Serial(
            port=com_port,
            baudrate=baud_rate,
            timeout=timeout,
            write_timeout=timeout,
        ) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            ser.write(payload)
            ser.flush()

            if not read_response:
                return b""

            time.sleep(0.05)
            return ser.read(ser.in_waiting or 128)

    except (serial.SerialException, OSError) as e:
        raise RuntimeError(
            "Could not open serial port '{0}'. "
            "Run `python relay.py list-ports` to see valid ports. "
            "Original error: {1}".format(com_port, e)
        ) from None


def send_all_relays(com_port, baud, state, timeout):
    _transact(com_port, baud, CMD["all"][state], timeout, read_response=False)


def pulse_all_relays(com_port, baud, seconds, timeout):
    send_all_relays(com_port, baud, "on", timeout)
    time.sleep(seconds)
    send_all_relays(com_port, baud, "off", timeout)


def send_one_relay(com_port, baud, relay_num, state, timeout):
    if relay_num < 1 or relay_num > 8:
        raise RuntimeError("Relay number must be between 1 and 8; got {0}".format(relay_num))
    _transact(com_port, baud, CMD["relay"][relay_num][state], timeout, read_response=False)


def pulse_one_relay(com_port, baud, relay_num, seconds, timeout):
    send_one_relay(com_port, baud, relay_num, "on", timeout)
    time.sleep(seconds)
    send_one_relay(com_port, baud, relay_num, "off", timeout)


def decode_status_ascii(response):
    """
    Device returns ASCII like:
      b"CH1:OFFCH2:ON...CH8:OFF"
    """
    text = response.decode("ascii", errors="ignore")
    matches = re.findall(r"CH(\d+):(ON|OFF)", text)
    if not matches:
        return None
    return {"ch{0}".format(ch): (1 if v == "ON" else 0) for ch, v in matches}


def parse_args(argv=None):
    # Global options parser: parsed first; ignores unknowns
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument("--port")
    global_parser.add_argument("--baud", type=int, default=DEFAULT_BAUD_RATE)
    global_parser.add_argument("--timeout", type=float, default=1.0)
    globals_ns, remaining = global_parser.parse_known_args(argv)

    # Command parser
    parser = argparse.ArgumentParser(description="USB Relay CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-ports")
    list_group = p_list.add_mutually_exclusive_group()
    list_group.add_argument("--detailed", action="store_true", help="Show detailed port info")
    list_group.add_argument("--csv", action="store_true", help="Output ports as CSV")
    list_group.add_argument("--json", action="store_true", help="Output ports as JSON")

    p_all = sub.add_parser("all")
    p_all.add_argument("state", choices=["on", "off", "pulse"])
    p_all.add_argument("--seconds", type=float, default=3.0)

    # NEW: control one relay
    p_relay = sub.add_parser("relay", help="Control a single relay")
    p_relay.add_argument("number", choices=RELAY_CHOICES, help="Relay number")
    p_relay.add_argument("state", choices=["on", "off", "pulse"], help="Desired state")
    p_relay.add_argument("--seconds", type=float, default=1.0, help="Pulse duration (seconds)")

    p_status = sub.add_parser("status")
    p_status.add_argument("target", choices=RELAY_CHOICES + ["all"])
    p_status.add_argument("--raw", action="store_true")

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

        if c.command == "all":
            if c.state in ("on", "off"):
                send_all_relays(com_port, g.baud, c.state, g.timeout)
                print("OK: all relays {0} ({1})".format(c.state, com_port))
                return 0

            pulse_all_relays(com_port, g.baud, c.seconds, g.timeout)
            print("OK: all relays pulse {0}s ({1})".format(c.seconds, com_port))
            return 0

        # NEW: single relay command handling
        if c.command == "relay":
            relay_num = int(c.number)

            if c.state in ("on", "off"):
                send_one_relay(com_port, g.baud, relay_num, c.state, g.timeout)
                print("OK: relay{0} {1} ({2})".format(relay_num, c.state, com_port))
                return 0

            pulse_one_relay(com_port, g.baud, relay_num, c.seconds, g.timeout)
            print("OK: relay{0} pulse {1}s ({2})".format(relay_num, c.seconds, com_port))
            return 0

        if c.command == "status":
            if c.target == "all":
                payload = CMD["query"]["all"]
            else:
                payload = CMD["query"][int(c.target)]

            resp = _transact(com_port, g.baud, payload, g.timeout, read_response=True)

            if c.raw:
                print("RAW:", resp.hex())

            decoded = decode_status_ascii(resp)
            if not decoded:
                print("Unable to decode status")
                return 1

            if c.target == "all":
                statuses = [
                    "relay{0}={1}".format(relay_num, decoded.get("ch{0}".format(relay_num)))
                    for relay_num in range(1, 9)
                ]
                print(" ".join(statuses))
            else:
                relay_num = int(c.target)
                print("relay{0}={1}".format(relay_num, decoded.get("ch{0}".format(relay_num))))
            return 0

        return 2

    except RuntimeError as e:
        print("ERROR:", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
