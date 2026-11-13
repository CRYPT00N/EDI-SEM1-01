"""Open-Meteo client with a 10 minute in-process cache.

Only three API values are required by the brief: temperature_2m,
relative_humidity_2m and precipitation. The modelled soil moisture at 0-7 cm is
requested as well because the fallback path needs a ground-moisture estimate
when the ESP32 goes offline; if the model does not publish it we fall back to a
documented regional default.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional

import httpx

from config import load_settings
from utils import ensure_utc, utc_now

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_SOIL_MOISTURE_PCT = 40.0
CURRENT_FIELDS = "temperature_2m,relative_humidity_2m,precipitation"
SOIL_FIELD = "soil_moisture_0_to_7cm"


class WeatherError(RuntimeError):
    """Raised when Open-Meteo cannot be reached or returns unexpected data."""


@dataclass(frozen=True)
class WeatherSnapshot:
    """Normalised Open-Meteo observation (temperatures in °C, moisture in %)."""

    temperature_c: float
    humidity_pct: float
    precipitation_mm: float
    soil_moisture_pct: float
    soil_moisture_estimated: bool
    observed_at: datetime
    fetched_at: datetime
    is_cached: bool = False
    latitude: float = 0.0
    longitude: float = 0.0


# (latitude, longitude) -> (monotonic fetch time, snapshot)
_cache: dict[tuple[float, float], tuple[float, WeatherSnapshot]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """Empty the weather cache (used between tests and by ``force_refresh``)."""
    with _cache_lock:
        _cache.clear()


def get_weather(
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    force_refresh: bool = False,
    now: Optional[datetime] = None,
) -> WeatherSnapshot:
    """Return the current Open-Meteo observation, cached for 10 minutes."""
    settings = load_settings()
    lat = settings.latitude if latitude is None else float(latitude)
    lon = settings.longitude if longitude is None else float(longitude)
    moment = ensure_utc(now or utc_now())

    key = (round(lat, 4), round(lon, 4))
    if not force_refresh:
        with _cache_lock:
            entry = _cache.get(key)
        if entry is not None:
            stored_at, snapshot = entry
            if time.time() - stored_at < settings.weather_cache_seconds:
                return replace(snapshot, is_cached=True)

    snapshot = _fetch(lat, lon, moment)
    with _cache_lock:
        _cache[key] = (time.time(), snapshot)
    return snapshot


def _fetch(latitude: float, longitude: float, now: datetime) -> WeatherSnapshot:
    """Perform the HTTPS request and normalise the JSON payload."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": CURRENT_FIELDS,
        "hourly": SOIL_FIELD,
        "forecast_days": 1,
        "timezone": "UTC",
    }
    try:
        response = httpx.get(OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise WeatherError(f"Open-Meteo request failed: {exc.__class__.__name__}") from exc
    except ValueError as exc:  # includes JSON decoding errors
        raise WeatherError("Open-Meteo returned a response that was not JSON") from exc

    return _parse(payload, latitude, longitude, now)


def _parse(
    payload: dict, latitude: float, longitude: float, now: datetime
) -> WeatherSnapshot:
    """Turn an Open-Meteo payload into a :class:`WeatherSnapshot`."""
    current = payload.get("current")
    if not isinstance(current, dict):
        raise WeatherError("Open-Meteo response is missing the 'current' block")

    try:
        temperature_c = float(current["temperature_2m"])
        humidity_pct = float(current["relative_humidity_2m"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError("Open-Meteo 'current' block is missing temperature/humidity") from exc

    precipitation_mm = _as_float(current.get("precipitation"), default=0.0)
    observed_at = _parse_time(current.get("time"), default=now)
    soil_moisture_pct, estimated = _soil_moisture(payload, current.get("time"))

    return WeatherSnapshot(
        temperature_c=round(temperature_c, 1),
        humidity_pct=round(humidity_pct, 1),
        precipitation_mm=round(precipitation_mm, 2),
        soil_moisture_pct=soil_moisture_pct,
        soil_moisture_estimated=estimated,
        observed_at=observed_at,
        fetched_at=now,
        latitude=latitude,
        longitude=longitude,
    )


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_time(value: object, default: datetime) -> datetime:
    if not isinstance(value, str) or not value:
        return default
    try:
        return ensure_utc(datetime.fromisoformat(value))
    except ValueError:
        return default


def _soil_moisture(payload: dict, current_time: object) -> tuple[float, bool]:
    """Return (soil moisture %, whether the value is a regional estimate).

    Open-Meteo publishes soil moisture as volumetric water content (m³/m³),
    which is converted to percent. Values that already look like a percentage
    are passed through unchanged, and a missing model value falls back to
    ``DEFAULT_SOIL_MOISTURE_PCT`` and is flagged as estimated.
    """
    hourly = payload.get("hourly")
    if isinstance(hourly, dict):
        times = hourly.get("time")
        values = hourly.get(SOIL_FIELD)
        if isinstance(times, list) and isinstance(values, list):
            target_hour = current_time[:13] if isinstance(current_time, str) else ""
            last_valid: Optional[float] = None
            for index, stamp in enumerate(times):
                if index >= len(values) or values[index] is None:
                    continue
                percentage = _to_percentage(values[index])
                if percentage is None:
                    continue
                if target_hour and isinstance(stamp, str) and stamp[:13] == target_hour:
                    return percentage, False
                last_valid = percentage
            if last_valid is not None:
                return last_valid, False
    return round(DEFAULT_SOIL_MOISTURE_PCT, 1), True


def _to_percentage(value: object) -> Optional[float]:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    percentage = number * 100.0 if number <= 1.0 else number
    return round(min(100.0, max(0.0, percentage)), 1)
