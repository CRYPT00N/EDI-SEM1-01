"""Unit tests for the dual-source fusion rules (Stage 2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fusion import (
    SOURCE_ESP32,
    SOURCE_FALLBACK,
    DeviceReading,
    FusionError,
    fuse,
)
from weather import WeatherSnapshot

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)


def device(**overrides) -> DeviceReading:
    """A valid ESP32 reading from two minutes ago."""
    values = {
        "temperature_c": 26.0,
        "humidity_pct": 55.0,
        "soil_moisture_pct": 42.0,
        "recorded_at": NOW - timedelta(minutes=2),
    }
    values.update(overrides)
    return DeviceReading(**values)


def weather() -> WeatherSnapshot:
    """A satellite snapshot with clearly different values from ``device``."""
    return WeatherSnapshot(
        temperature_c=30.5,
        humidity_pct=48.0,
        precipitation_mm=0.0,
        soil_moisture_pct=35.0,
        soil_moisture_estimated=False,
        observed_at=NOW,
        fetched_at=NOW,
    )


def test_primary_device_reading_is_used_when_fresh_and_valid():
    result = fuse(device(), None, None, now=NOW)

    assert result.source == SOURCE_ESP32
    assert result.temperature_c == 26.0
    assert result.humidity_pct == 55.0
    assert result.soil_moisture_pct == 42.0
    assert "within valid ranges" in result.fusion_note
    assert result.fallback_reasons == ()


def test_reading_thirteen_minutes_old_is_still_primary():
    result = fuse(device(recorded_at=NOW - timedelta(minutes=13)), None, None, now=NOW)

    assert result.source == SOURCE_ESP32


def test_missing_device_reading_falls_back_to_weather():
    result = fuse(None, None, weather(), now=NOW)

    assert result.source == SOURCE_FALLBACK
    assert result.temperature_c == 30.5
    assert result.humidity_pct == 48.0
    assert result.soil_moisture_pct == 35.0
    assert "no ESP32 reading has arrived in the last 15 minutes" in result.fusion_note
    assert result.fallback_reasons


def test_stale_device_reading_falls_back_to_weather():
    result = fuse(device(recorded_at=NOW - timedelta(minutes=22)), None, weather(), now=NOW)

    assert result.source == SOURCE_FALLBACK
    assert "22.0 min old" in result.fusion_note
    assert result.temperature_c == 30.5


@pytest.mark.parametrize(
    "overrides,label",
    [
        ({"temperature_c": 95.0}, "temperature"),
        ({"humidity_pct": 140.0}, "humidity"),
        ({"soil_moisture_pct": -5.0}, "soil moisture"),
    ],
)
def test_out_of_range_device_values_fall_back_to_weather(overrides, label):
    result = fuse(device(**overrides), None, weather(), now=NOW)

    assert result.source == SOURCE_FALLBACK
    assert "outside the valid range" in result.fusion_note
    assert label in result.fusion_note
    # The fused values come from Open-Meteo, not from the bad probe.
    assert result.temperature_c == 30.5


def test_previous_reading_is_included_in_the_primary_note():
    previous = device(soil_moisture_pct=40.0, recorded_at=NOW - timedelta(minutes=32))
    result = fuse(device(soil_moisture_pct=43.5), previous, None, now=NOW)

    assert result.source == SOURCE_ESP32
    assert "+3.5 pp" in result.fusion_note


def test_large_positive_soil_moisture_spike_falls_back():
    previous = device(soil_moisture_pct=20.0, recorded_at=NOW - timedelta(minutes=32))
    result = fuse(device(soil_moisture_pct=65.0), previous, weather(), now=NOW)

    assert result.source == SOURCE_FALLBACK
    assert result.fallback_reasons
    assert "sensor spike" in result.fusion_note
    assert "+45.0 percentage points" in result.fusion_note


def test_large_negative_soil_moisture_spike_falls_back():
    previous = device(soil_moisture_pct=80.0, recorded_at=NOW - timedelta(minutes=32))
    result = fuse(device(soil_moisture_pct=30.0), previous, weather(), now=NOW)

    assert result.source == SOURCE_FALLBACK
    assert "-50.0 percentage points" in result.fusion_note


def test_change_of_exactly_forty_points_is_not_a_spike():
    previous = device(soil_moisture_pct=25.0, recorded_at=NOW - timedelta(minutes=32))
    result = fuse(device(soil_moisture_pct=65.0), previous, None, now=NOW)

    assert result.source == SOURCE_ESP32


def test_spike_check_is_skipped_for_adjustable_threshold():
    previous = device(soil_moisture_pct=20.0, recorded_at=NOW - timedelta(minutes=32))
    result = fuse(
        device(soil_moisture_pct=45.0),
        previous,
        None,
        now=NOW,
        spike_threshold_pct=50.0,
    )

    assert result.source == SOURCE_ESP32


def test_fallback_without_weather_raises_a_clear_error():
    with pytest.raises(FusionError):
        fuse(None, None, None, now=NOW)


def test_fallback_note_marks_an_estimated_soil_moisture_value():
    snapshot = WeatherSnapshot(
        temperature_c=30.0,
        humidity_pct=50.0,
        precipitation_mm=0.0,
        soil_moisture_pct=40.0,
        soil_moisture_estimated=True,
        observed_at=NOW,
        fetched_at=NOW,
        is_cached=True,
    )
    result = fuse(None, None, snapshot, now=NOW)

    assert "regional default" in result.fusion_note
    assert "10 minute cache" in result.fusion_note
