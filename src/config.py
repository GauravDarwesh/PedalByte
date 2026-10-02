"""Runtime configuration for PedalByte."""

import os


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


SERIAL_PORT = os.getenv("PEDALBYTE_SERIAL_PORT", "")
BAUDRATE = _int_env("PEDALBYTE_BAUDRATE", 115200)
VIRTUAL_DRIVE_RATIO = _float_env("PEDALBYTE_VIRTUAL_DRIVE_RATIO", 3.5)
VIRTUAL_WHEEL_CIRCUMFERENCE_M = _float_env("PEDALBYTE_VIRTUAL_WHEEL_CIRCUMFERENCE_M", 2.1)
USER_WEIGHT_KG = _float_env("PEDALBYTE_USER_WEIGHT_KG", 70.0)
USER_AGE = _int_env("PEDALBYTE_USER_AGE", 30)
ZONE_LOW_BPM = _int_env("PEDALBYTE_ZONE_LOW_BPM", 118)
ZONE_HIGH_BPM = _int_env("PEDALBYTE_ZONE_HIGH_BPM", 138)
CADENCE_DEVICE_NAME = os.getenv("PEDALBYTE_CADENCE_NAME", "PedalByte Cadence")
POWER_DEVICE_NAME = os.getenv("PEDALBYTE_POWER_NAME", "PedalByte Power")
HEART_RATE_NAME_HINTS = tuple(x.strip().lower() for x in os.getenv("PEDALBYTE_HR_NAME_HINTS", "").split(",") if x.strip())
HEART_RATE_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
