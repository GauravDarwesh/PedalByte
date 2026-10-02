"""Read and validate the MCU telemetry line protocol.

Expected line format:
    RPM:82.4 Level:5 Watts:147

The protocol is intentionally bike-agnostic. The upstream controller can be an
Arduino, ESP32, Raspberry Pi Pico, or another serial-capable adapter.
"""

import math
import re

import serial


PATTERN = re.compile(
    r"^\s*RPM:(?P<rpm>[0-9]+(?:\.[0-9]+)?)\s+"
    r"Level:(?P<level>[0-9]+)\s+"
    r"Watts:(?P<watts>[0-9]+(?:\.[0-9]+)?)\s*$"
)


class SerialReader:
    def __init__(self, port: str, baudrate: int, state):
        self.ser = serial.Serial(port, baudrate, timeout=1)
        self.state = state
        print(f"[SERIAL] Connected to adapter on {port}")

    def update(self) -> None:
        try:
            line = self.ser.readline().decode("utf-8", errors="ignore").strip()
            match = PATTERN.fullmatch(line)
            if not match:
                return

            rpm = float(match.group("rpm"))
            level = int(match.group("level"))
            watts = float(match.group("watts"))

            # Reject non-finite or impossible telemetry before it reaches the
            # shared state and downstream dashboard/BLE calculations.
            if not all(math.isfinite(value) for value in (rpm, watts)):
                return
            if rpm < 0 or watts < 0 or level < 0:
                return

            self.state.rpm = rpm
            self.state.level = level
            self.state.watts = watts
        except (OSError, ValueError, serial.SerialException):
            pass
