"""Read a simple line protocol from an MCU / sensor bridge.

Expected line format:
    RPM:82.4 Level:5 Watts:147

The protocol is intentionally bike-agnostic. The upstream controller can be an
Arduino, ESP32, Raspberry Pi Pico, or another serial-capable adapter.
"""

import re

import serial


PATTERN = re.compile(
    r"RPM:(?P<rpm>[0-9.]+)\s+Level:(?P<level>\d+)\s+Watts:(?P<watts>[0-9.]+)"
)


class SerialReader:
    def __init__(self, port: str, baudrate: int, state):
        self.ser = serial.Serial(port, baudrate, timeout=1)
        self.state = state
        print(f"[SERIAL] Connected to adapter on {port}")

    def update(self) -> None:
        try:
            line = self.ser.readline().decode("utf-8", errors="ignore").strip()
            match = PATTERN.search(line)
            if not match:
                return

            self.state.rpm = float(match.group("rpm"))
            self.state.level = int(match.group("level"))
            self.state.watts = float(match.group("watts"))
        except (OSError, ValueError, serial.SerialException):
            pass
