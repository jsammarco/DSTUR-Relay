import argparse
import re
import time

import serial
from serial.tools import list_ports


DEFAULT_BAUD_RATE = 9600

CMD = {
    "relay": {
        1: {"on": bytes([0xA0, 0x01, 0x01, 0xA2]), "off": bytes([0xA0, 0x01, 0x00, 0xA1])},
        2: {"on": bytes([0xA0, 0x02, 0x01, 0xA3]), "off": bytes([0xA0, 0x02, 0x00, 0xA2])},
    },
    "all": {
        "on": bytes([0xA0, 0x0F, 0x01, 0xB0]),
        "off": bytes([0xA0, 0x0F, 0x00, 0xAF]),
    },
    "query": {
        1: bytes([0xA0, 0x01, 0x02, 0xA3]),
        2: bytes([0xA0, 0x02, 0x02, 0xA4]),
        "all": bytes([0xA0, 0x0F, 0x02, 0xB1]),
    },
}


def get_available_serial_ports():
    return [p.device for p in list_ports.comports()]


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

    sub.add_parser("list-ports")

    p_all = sub.add_parser("all")
    p_all.add_argument("state", choices=["on", "off", "pulse"])
    p_all.add_argument("--seconds", type=float, default=3.0)

    p_status = sub.add_parser("status")
    p_status.add_argument("target", choices=["1", "2", "all"])
    p_status.add_argument("--raw", action="store_true")

    cmd_ns = parser.parse_args(remaining)
    return globals_ns, cmd_ns


def main(argv=None):
    try:
        g, c = parse_args(argv)

        if c.command == "list-ports":
            for p in get_available_serial_ports():
                print(p)
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

            if c.target == "1":
                print("relay1={0}".format(decoded.get("ch1")))
            elif c.target == "2":
                print("relay2={0}".format(decoded.get("ch2")))
            else:
                print("relay1={0} relay2={1}".format(decoded.get("ch1"), decoded.get("ch2")))
            return 0

        return 2

    except RuntimeError as e:
        print("ERROR:", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
