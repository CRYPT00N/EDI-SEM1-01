"""Tests for the crop catalogue exposed to the dashboard (multi-crop support)."""

from __future__ import annotations


def test_crops_endpoint_lists_every_supported_crop(client):
    body = client.get("/api/crops").json()

    names = [crop["name"] for crop in body["crops"]]
    assert body["count"] == len(names)
    assert len(names) >= 10
    for required in ("Wheat", "Rice", "Maize", "Cotton", "Soybeans", "Tomato"):
        assert required in names


def test_crops_endpoint_returns_the_band_structure_the_ui_needs(client):
    body = client.get("/api/crops").json()
    wheat = next(crop for crop in body["crops"] if crop["name"] == "Wheat")

    assert wheat["temperature"] == {"optimal": [15, 25], "marginal": [10, 32]}
    assert wheat["humidity"]["optimal"] == [40, 70]
    assert wheat["soil_moisture"]["marginal"] == [25, 70]
    # Advice text stays server side; the catalogue is only ranges.
    assert "advice" not in wheat


def test_advisory_works_for_a_newly_added_crop(client):
    # Rice: 28 °C, 80 % humidity, 70 % soil moisture -> all optimal.
    client.post(
        "/api/telemetry",
        json={"temp": 28.0, "humidity": 80.0, "soil_moisture": 70.0},
        headers={"X-API-Key": "test-device-key"},
    )

    body = client.get("/api/advisory", params={"crop": "Rice"}).json()

    assert body["crop"] == "Rice"
    assert body["viability"] == "Optimal"
    assert "Rice" in body["recommendation"]
