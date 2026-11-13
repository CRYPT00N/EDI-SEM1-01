"""Simulation mode.

Set ``SIMULATE=true`` to let the dashboard run without any hardware. Synthetic
readings follow a smooth random walk (with a small daily temperature curve) so
the cards and the advisory behave like a real field node. Turning simulation off
is a single environment variable change.
"""

from __future__ import annotations

import math
import random
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fusion import DeviceReading
from utils import ensure_utc, utc_now

# Baseline conditions that produce sensible verdicts for all three crops.
MEAN_TEMPERATURE_C = 26.0
MEAN_HUMIDITY_PCT = 57.0
MEAN_SOIL_MOISTURE_PCT = 46.0

_state = {
    "temperature_c": MEAN_TEMPERATURE_C,
    "humidity_pct": MEAN_HUMIDITY_PCT,
    "soil_moisture_pct": MEAN_SOIL_MOISTURE_PCT,
}
_state_lock = threading.Lock()


@dataclass(frozen=True)
class SimulatedReading(DeviceReading):
    """A synthetic reading, typed like a real device reading for fusion."""


def reset_state() -> None:
    """Return the simulator to its baseline (used by tests and demos)."""
    with _state_lock:
        _state["temperature_c"] = MEAN_TEMPERATURE_C
        _state["humidity_pct"] = MEAN_HUMIDITY_PCT
        _state["soil_moisture_pct"] = MEAN_SOIL_MOISTURE_PCT


def generate_reading(
    now: Optional[datetime] = None, rng: Optional[random.Random] = None
) -> SimulatedReading:
    """Produce one realistic ESP32-shaped reading."""
    generator = rng or random
    moment = ensure_utc(now or utc_now())

    # A gentle day/night curve: warmest in the mid-afternoon, coolest at night.
    hour = moment.hour + moment.minute / 60.0
    daily_offset = 3.0 * _sin_curve(hour)

    with _state_lock:
        temperature = _walk(
            _state["temperature_c"],
            MEAN_TEMPERATURE_C + daily_offset,
            spread=0.5,
            rng=generator,
        )
        humidity = _walk(
            _state["humidity_pct"],
            MEAN_HUMIDITY_PCT - daily_offset * 1.5,
            spread=1.2,
            rng=generator,
        )
        soil = _walk(
            _state["soil_moisture_pct"],
            MEAN_SOIL_MOISTURE_PCT,
            spread=1.0,
            rng=generator,
        )

        _state["temperature_c"] = temperature
        _state["humidity_pct"] = humidity
        _state["soil_moisture_pct"] = soil

    return SimulatedReading(
        temperature_c=round(temperature, 1),
        humidity_pct=round(humidity, 1),
        soil_moisture_pct=round(soil, 1),
        recorded_at=moment,
    )


def _walk(current: float, target: float, *, spread: float, rng: random.Random) -> float:
    """Mean-reverting random walk, clamped to physically sensible values."""
    mean_reversion = (target - current) * 0.15
    noise = rng.uniform(-spread, spread)
    return _clamp(current + mean_reversion + noise, 12.0, 82.0)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sin_curve(hour: float) -> float:
    """Normalised sine wave peaking around 14:00 (returns -1.0 .. 1.0)."""
    return math.sin((hour - 8.0) / 24.0 * 2.0 * math.pi)
