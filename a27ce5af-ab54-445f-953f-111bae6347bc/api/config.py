"""Environment driven configuration for AgriCloud-CI.

Nothing secret is ever written in the code: every deployment specific value is
read from an environment variable so the same source runs locally, in Docker,
in GitHub Actions and on Vercel.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Defaults for the optional tuning knobs. They are documented in README.md and
# .env.example so a reviewer can see where every number comes from.
DEFAULT_LATITUDE = 18.5204  # Pune, Maharashtra (VIT campus)
DEFAULT_LONGITUDE = 73.8567
DEFAULT_FUSION_MAX_AGE_MINUTES = 15.0
DEFAULT_SPIKE_THRESHOLD_PCT = 40.0
DEFAULT_WEATHER_CACHE_SECONDS = 600  # 10 minutes, required by the brief


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, received {raw!r}") from exc


def _get_int(name: str, default: int) -> int:
    return int(_get_float(name, float(default)))


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """A snapshot of the environment, built fresh on every call.

    Reading the environment per call (instead of caching at import time) keeps
    tests and the Vercel cold start simple: change an env var, see the effect.
    """

    device_api_key: str
    database_url: str
    latitude: float
    longitude: float
    simulate: bool
    fusion_max_age_minutes: float
    spike_threshold_pct: float
    weather_cache_seconds: int


def load_settings() -> Settings:
    """Read the current environment into a :class:`Settings` object."""
    return Settings(
        device_api_key=os.getenv("DEVICE_API_KEY", "").strip(),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        latitude=_get_float("LATITUDE", DEFAULT_LATITUDE),
        longitude=_get_float("LONGITUDE", DEFAULT_LONGITUDE),
        simulate=_get_bool("SIMULATE", False),
        fusion_max_age_minutes=_get_float(
            "FUSION_MAX_AGE_MINUTES", DEFAULT_FUSION_MAX_AGE_MINUTES
        ),
        spike_threshold_pct=_get_float("SPIKE_THRESHOLD_PCT", DEFAULT_SPIKE_THRESHOLD_PCT),
        weather_cache_seconds=_get_int("WEATHER_CACHE_SECONDS", DEFAULT_WEATHER_CACHE_SECONDS),
    )
