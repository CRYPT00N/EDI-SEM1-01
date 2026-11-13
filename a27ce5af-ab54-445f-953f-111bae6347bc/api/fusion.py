"""Dual-source sensor fusion.

The ESP32 is the primary source. The cloud falls back to Open-Meteo when:

1. no ESP32 reading has arrived in the last 15 minutes (device/Wi-Fi down),
2. the device sent a value outside the valid range, or
3. soil moisture jumped by more than 40 percentage points since the previous
   reading, which almost always means a loose probe or an air pocket.

Every fused result records the source that was used and why in ``fusion_note``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from config import load_settings
from utils import ensure_utc, utc_now
from weather import WeatherSnapshot

SOURCE_ESP32 = "esp32"
SOURCE_FALLBACK = "satellite_fallback"

# Valid physical ranges accepted from the device (also enforced by the POST body).
VALID_RANGES: dict[str, tuple[float, float]] = {
    "temperature_c": (-20.0, 60.0),
    "humidity_pct": (0.0, 100.0),
    "soil_moisture_pct": (0.0, 100.0),
}

METRIC_LABELS = {
    "temperature_c": "temperature",
    "humidity_pct": "humidity",
    "soil_moisture_pct": "soil moisture",
}


class FusionError(RuntimeError):
    """Raised when the fallback is needed but no weather snapshot is available."""


@dataclass(frozen=True)
class DeviceReading:
    """One reading received from the ESP32 (or from simulation mode)."""

    temperature_c: float
    humidity_pct: float
    soil_moisture_pct: float
    recorded_at: datetime


@dataclass(frozen=True)
class FusedReading:
    """The reading the platform actually uses, plus a human readable trace."""

    temperature_c: float
    humidity_pct: float
    soil_moisture_pct: float
    source: str
    fusion_note: str
    timestamp: datetime
    fallback_reasons: tuple[str, ...] = ()


def fuse(
    device_reading: Optional[DeviceReading] = None,
    previous_reading: Optional[DeviceReading] = None,
    weather: Optional[WeatherSnapshot] = None,
    now: Optional[datetime] = None,
    max_age_minutes: Optional[float] = None,
    spike_threshold_pct: Optional[float] = None,
) -> FusedReading:
    """Combine the device reading and Open-Meteo into one fused reading."""
    settings = load_settings()
    moment = ensure_utc(now or utc_now())
    max_age = (
        settings.fusion_max_age_minutes if max_age_minutes is None else max_age_minutes
    )
    spike_threshold = (
        settings.spike_threshold_pct
        if spike_threshold_pct is None
        else spike_threshold_pct
    )

    reasons = _device_problems(
        device_reading, previous_reading, moment, max_age, spike_threshold
    )

    if reasons:
        if weather is None:
            raise FusionError(
                "ESP32 data cannot be trusted and Open-Meteo is unavailable: "
                + "; ".join(reasons)
            )
        return _fallback_reading(weather, reasons, moment)

    # Defensive: mypy/type checkers cannot see that reasons == [] implies the
    # reading exists, so make the assumption explicit for readers.
    assert device_reading is not None
    age_minutes = max(
        0.0, (moment - ensure_utc(device_reading.recorded_at)).total_seconds() / 60.0
    )
    note = (
        f"Primary ESP32 reading accepted (age {age_minutes:.1f} min, all values "
        "within valid ranges"
    )
    if previous_reading is not None:
        delta = device_reading.soil_moisture_pct - previous_reading.soil_moisture_pct
        note += f", soil moisture change {delta:+.1f} pp (spike limit {spike_threshold:.0f} pp)"
    note += ")."

    return FusedReading(
        temperature_c=round(device_reading.temperature_c, 1),
        humidity_pct=round(device_reading.humidity_pct, 1),
        soil_moisture_pct=round(device_reading.soil_moisture_pct, 1),
        source=SOURCE_ESP32,
        fusion_note=note,
        timestamp=moment,
    )


def _device_problems(
    device_reading: Optional[DeviceReading],
    previous_reading: Optional[DeviceReading],
    now: datetime,
    max_age_minutes: float,
    spike_threshold_pct: float,
) -> list[str]:
    """Explain why the ESP32 reading cannot be trusted (empty list if it can)."""
    if device_reading is None:
        return [
            f"no ESP32 reading has arrived in the last {max_age_minutes:.0f} minutes"
        ]

    reasons: list[str] = []
    age_minutes = (now - ensure_utc(device_reading.recorded_at)).total_seconds() / 60.0
    if age_minutes > max_age_minutes:
        reasons.append(
            f"last ESP32 reading is {age_minutes:.1f} min old "
            f"(limit {max_age_minutes:.0f} min)"
        )

    for field, (low, high) in VALID_RANGES.items():
        value = getattr(device_reading, field)
        if value is None or not low <= value <= high:
            reasons.append(
                f"ESP32 {METRIC_LABELS[field]} value {value} is outside the valid "
                f"range {low:.0f}..{high:.0f}"
            )

    # A spike only makes sense when the current reading itself is usable.
    if not reasons and previous_reading is not None:
        delta = device_reading.soil_moisture_pct - previous_reading.soil_moisture_pct
        if abs(delta) > spike_threshold_pct:
            reasons.append(
                f"soil moisture changed {delta:+.1f} percentage points since the "
                f"previous reading (sensor spike, limit {spike_threshold_pct:.0f})"
            )

    return reasons


def _fallback_reading(
    weather: WeatherSnapshot, reasons: list[str], now: datetime
) -> FusedReading:
    """Build the satellite fallback reading and its audit note."""
    note = "Satellite fallback (Open-Meteo): " + "; ".join(reasons) + "."
    note += f" Grid observation from {weather.observed_at.isoformat()}."
    if weather.soil_moisture_estimated:
        note += " Soil moisture is a regional default because the model value was missing."
    if weather.is_cached:
        note += " Weather snapshot served from the 10 minute cache."

    return FusedReading(
        temperature_c=round(weather.temperature_c, 1),
        humidity_pct=round(weather.humidity_pct, 1),
        soil_moisture_pct=round(weather.soil_moisture_pct, 1),
        source=SOURCE_FALLBACK,
        fusion_note=note,
        timestamp=now,
        fallback_reasons=tuple(reasons),
    )
