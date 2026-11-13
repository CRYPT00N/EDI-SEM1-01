"""Tests for the Open-Meteo client: parsing, caching and error handling (Stage 1)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

import weather

NOW = datetime(2026, 11, 12, 10, 30, tzinfo=timezone.utc)

PAYLOAD = {
    "current": {
        "time": "2026-11-12T10:30",
        "temperature_2m": 28.4,
        "relative_humidity_2m": 57,
        "precipitation": 0.2,
    },
    "hourly": {
        "time": ["2026-11-12T09:00", "2026-11-12T10:00", "2026-11-12T11:00"],
        "soil_moisture_0_to_7cm": [0.21, 0.24, None],
    },
}


@pytest.fixture(autouse=True)
def clear_weather_cache():
    weather.clear_cache()
    yield
    weather.clear_cache()


def test_parse_reads_the_current_observation():
    snapshot = weather._parse(PAYLOAD, 18.52, 73.86, NOW)

    assert snapshot.temperature_c == 28.4
    assert snapshot.humidity_pct == 57.0
    assert snapshot.precipitation_mm == 0.2
    assert snapshot.observed_at == NOW
    assert snapshot.is_cached is False


def test_parse_converts_volumetric_soil_moisture_to_percent():
    snapshot = weather._parse(PAYLOAD, 18.52, 73.86, NOW)

    # 0.24 m³/m³ for the 10:00 hour becomes 24 %.
    assert snapshot.soil_moisture_pct == 24.0
    assert snapshot.soil_moisture_estimated is False


def test_missing_model_soil_moisture_is_marked_as_estimated():
    payload = {"current": PAYLOAD["current"], "hourly": {"time": [], "soil_moisture_0_to_7cm": []}}

    snapshot = weather._parse(payload, 18.52, 73.86, NOW)

    assert snapshot.soil_moisture_pct == weather.DEFAULT_SOIL_MOISTURE_PCT
    assert snapshot.soil_moisture_estimated is True


def test_missing_current_block_raises_weather_error():
    with pytest.raises(weather.WeatherError):
        weather._parse({"hourly": {}}, 18.52, 73.86, NOW)


def test_http_failure_is_reported_as_weather_error(monkeypatch):
    def _broken(*_args, **_kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(weather.httpx, "get", _broken)

    with pytest.raises(weather.WeatherError):
        weather._fetch(18.52, 73.86, NOW)


def test_weather_is_cached_for_ten_minutes(monkeypatch):
    calls = []

    def _fake_fetch(latitude, longitude, now):
        calls.append((latitude, longitude))
        return weather._parse(PAYLOAD, latitude, longitude, now)

    monkeypatch.setattr(weather, "_fetch", _fake_fetch)

    first = weather.get_weather(18.52, 73.86, now=NOW)
    second = weather.get_weather(18.52, 73.86, now=NOW)

    assert len(calls) == 1
    assert first.is_cached is False
    assert second.is_cached is True
    assert second.temperature_c == first.temperature_c


def test_force_refresh_bypasses_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(
        weather,
        "_fetch",
        lambda latitude, longitude, now: calls.append(1)
        or weather._parse(PAYLOAD, latitude, longitude, now),
    )

    weather.get_weather(18.52, 73.86, now=NOW)
    weather.get_weather(18.52, 73.86, force_refresh=True, now=NOW)

    assert len(calls) == 2


def test_cache_expires_after_the_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(
        weather,
        "_fetch",
        lambda latitude, longitude, now: calls.append(1)
        or weather._parse(PAYLOAD, latitude, longitude, now),
    )
    clock = {"now": 1000.0}
    monkeypatch.setattr(weather.time, "time", lambda: clock["now"])

    weather.get_weather(18.52, 73.86, now=NOW)
    clock["now"] += 601  # eleven minutes later
    weather.get_weather(18.52, 73.86, now=NOW)

    assert len(calls) == 2
