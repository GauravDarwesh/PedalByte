from dataclasses import dataclass


@dataclass
class BikeState:
    """Current telemetry snapshot shared by the workers."""

    rpm: float = 0.0
    watts: float = 0.0
    level: int = 0
    heart_rate: int = 0
    total_wheel_revs: float = 0.0
