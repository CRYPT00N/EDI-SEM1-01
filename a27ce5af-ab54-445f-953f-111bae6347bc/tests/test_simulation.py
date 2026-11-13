"""Tests for simulation mode (Stage 6)."""

from __future__ import annotations

import random

import pytest

import simulation
from fusion import VALID_RANGES


@pytest.fixture(autouse=True)
def reset_simulator():
    simulation.reset_state()
    yield
    simulation.reset_state()


def test_simulated_readings_stay_inside_the_valid_device_ranges():
    generator = random.Random(7)

    for _ in range(200):
        reading = simulation.generate_reading(rng=generator)
        for field, (low, high) in VALID_RANGES.items():
            assert low <= getattr(reading, field) <= high


def test_simulated_readings_do_not_look_like_sensor_spikes():
    generator = random.Random(11)
    previous = simulation.generate_reading(rng=generator)

    for _ in range(100):
        reading = simulation.generate_reading(rng=generator)
        assert abs(reading.soil_moisture_pct - previous.soil_moisture_pct) <= 40.0
        previous = reading


def test_simulation_produces_changing_values():
    generator = random.Random(3)
    readings = [simulation.generate_reading(rng=generator) for _ in range(12)]

    assert len({reading.temperature_c for reading in readings}) > 1
    assert len({reading.soil_moisture_pct for reading in readings}) > 1
