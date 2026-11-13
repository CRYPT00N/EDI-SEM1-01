"""Endpoint tests (Stages 1 and 4): auth, validation, fusion wiring, persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import database

HEADERS = {"X-API-Key": "test-device-key"}
READING = {"temp": 22.0, "humidity": 55.0, "soil_moisture": 45.0}


def post_reading(client, payload=None, headers=None):
    """POST a telemetry sample with the shared device key by default."""
    return client.post(
        "/api/telemetry",
        json=READING if payload is None else payload,
        headers=HEADERS if headers is None else headers,
    )


# ---------------------------------------------------------------------------
# POST /api/telemetry
# ---------------------------------------------------------------------------
def test_telemetry_without_api_key_is_rejected(client):
    response = client.post("/api/telemetry", json=READING)

    assert response.status_code == 401
    assert "X-API-Key" in response.json()["detail"]


def test_telemetry_with_wrong_api_key_is_rejected(client):
    response = post_reading(client, headers={"X-API-Key": "definitely-wrong"})

    assert response.status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"temp": 61.0, "humidity": 55.0, "soil_moisture": 45.0},
        {"temp": -21.0, "humidity": 55.0, "soil_moisture": 45.0},
        {"temp": 22.0, "humidity": 101.0, "soil_moisture": 45.0},
        {"temp": 22.0, "humidity": 55.0, "soil_moisture": -0.5},
        {"temp": 22.0, "humidity": 55.0},
    ],
)
def test_telemetry_rejects_invalid_payloads(client, payload):
    response = post_reading(client, payload=payload)

    assert response.status_code == 422


def test_valid_reading_is_stored_with_a_timestamp(client):
    response = post_reading(client)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "saved"
    assert body["source"] == "esp32"
    assert body["received_at"].endswith("Z")

    history = client.get("/api/history").json()
    assert history["count"] == 1
    assert history["logs"][0]["temperature_c"] == 22.0
    assert history["logs"][0]["source"] == "esp32"


# ---------------------------------------------------------------------------
# GET /api/live
# ---------------------------------------------------------------------------
def test_live_uses_the_esp32_reading_when_it_is_fresh(client):
    post_reading(client)

    response = client.get("/api/live")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "esp32"
    assert body["temperature_c"] == 22.0
    assert body["soil_moisture_pct"] == 45.0
    assert "within valid ranges" in body["fusion_note"]
    assert body["simulate"] is False


def test_live_falls_back_to_satellite_without_any_device_reading(client, fake_weather):
    response = client.get("/api/live")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "satellite_fallback"
    assert body["temperature_c"] == fake_weather.temperature_c
    assert body["weather"]["humidity_pct"] == fake_weather.humidity_pct
    assert "no ESP32 reading has arrived" in body["fusion_note"]


def test_live_falls_back_when_the_last_reading_is_stale(client, fake_weather, insert_log):
    stale = datetime.now(timezone.utc) - timedelta(minutes=25)
    insert_log(
        source="esp32",
        temperature_c=40.0,
        humidity_pct=30.0,
        soil_moisture_pct=10.0,
        created_at=stale,
    )

    body = client.get("/api/live").json()

    assert body["source"] == "satellite_fallback"
    assert body["temperature_c"] == fake_weather.temperature_c
    assert "25.0 min old" in body["fusion_note"]


def test_live_falls_back_when_soil_moisture_spikes(client, fake_weather, insert_log):
    now = datetime.now(timezone.utc)
    insert_log(
        source="esp32",
        temperature_c=24.0,
        humidity_pct=60.0,
        soil_moisture_pct=20.0,
        created_at=now - timedelta(minutes=10),
    )
    insert_log(
        source="esp32",
        temperature_c=24.0,
        humidity_pct=60.0,
        soil_moisture_pct=85.0,
        created_at=now,
    )

    body = client.get("/api/live").json()

    assert body["source"] == "satellite_fallback"
    assert "sensor spike" in body["fusion_note"]


def test_live_reports_503_when_nothing_can_be_trusted_and_weather_is_down(client):
    # The autouse fixture makes every weather call fail.
    response = client.get("/api/live")

    assert response.status_code == 503
    assert "Open-Meteo" in response.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/advisory
# ---------------------------------------------------------------------------
def test_advisory_returns_and_saves_an_optimal_verdict(client):
    post_reading(client)  # 22 °C, 55 %, 45 % -> optimal for Wheat

    response = client.get("/api/advisory", params={"crop": "Wheat"})

    assert response.status_code == 200
    body = response.json()
    assert body["crop"] == "Wheat"
    assert body["viability"] == "Optimal"
    assert "optimal" in body["recommendation"].lower()
    assert body["conditions"]["soil_moisture_pct"] == 45.0
    assert body["log_id"] > 0

    saved = client.get("/api/history", params={"crop": "Wheat"}).json()["logs"][0]
    assert saved["viability"] == "Optimal"
    assert saved["crop"] == "Wheat"
    assert saved["recommendation"] == body["recommendation"]


def test_advisory_detects_a_marginal_verdict(client):
    post_reading(client, payload={"temp": 29.0, "humidity": 55.0, "soil_moisture": 45.0})

    body = client.get("/api/advisory", params={"crop": "Wheat"}).json()

    assert body["viability"] == "Marginal"
    assert body["metric_status"]["temperature"] == "marginal_high"


def test_advisory_detects_an_unsuitable_verdict(client):
    post_reading(client, payload={"temp": 45.0, "humidity": 55.0, "soil_moisture": 45.0})

    body = client.get("/api/advisory", params={"crop": "Cotton"}).json()

    assert body["viability"] == "Unsuitable"
    assert body["recommendation"].startswith("Critical:")


def test_advisory_rejects_an_unknown_crop(client):
    response = client.get("/api/advisory", params={"crop": "Coffee"})

    assert response.status_code == 400
    assert "Wheat" in response.json()["detail"]
    assert "Coffee" in response.json()["detail"]


def test_advisory_can_use_the_satellite_fallback(client, fake_weather):
    body = client.get("/api/advisory", params={"crop": "Soybeans"}).json()

    assert body["source"] == "satellite_fallback"
    assert body["conditions"]["temperature_c"] == fake_weather.temperature_c
    saved = client.get("/api/history").json()["logs"][0]
    assert saved["source"] == "satellite_fallback"


# ---------------------------------------------------------------------------
# GET /api/history
# ---------------------------------------------------------------------------
def test_history_is_newest_first_and_respects_the_limit(client):
    for temperature in (20.0, 21.0, 22.0):
        post_reading(client, payload={"temp": temperature, "humidity": 55.0, "soil_moisture": 45.0})

    body = client.get("/api/history", params={"limit": 2}).json()

    assert body["count"] == 2
    assert body["logs"][0]["temperature_c"] == 22.0
    assert body["logs"][1]["temperature_c"] == 21.0


def test_history_crop_filter_is_case_insensitive(client):
    post_reading(client)
    client.get("/api/advisory", params={"crop": "Wheat"})
    client.get("/api/advisory", params={"crop": "Cotton"})

    wheat = client.get("/api/history", params={"crop": "wheat"}).json()

    assert wheat["count"] == 1
    assert wheat["logs"][0]["crop"] == "Wheat"
    assert wheat["logs"][0]["viability"] == "Optimal"


# ---------------------------------------------------------------------------
# Simulation mode and health
# ---------------------------------------------------------------------------
def test_simulation_mode_serves_a_reading_without_hardware(client, monkeypatch):
    monkeypatch.setenv("SIMULATE", "true")

    body = client.get("/api/live").json()

    assert body["simulate"] is True
    assert body["source"] == "esp32"
    assert 0.0 <= body["soil_moisture_pct"] <= 100.0
    # Weather is offline in tests, so this also proves demo mode works offline.
    assert body["weather"] is None


def test_simulation_mode_is_recorded_in_the_advisory_note(client, monkeypatch):
    monkeypatch.setenv("SIMULATE", "true")

    body = client.get("/api/advisory", params={"crop": "Wheat"}).json()

    assert "Demo mode" in body["fusion_note"]
    assert body["viability"] in {"Optimal", "Marginal", "Unsuitable"}


def test_health_reports_simulation_and_database_configuration(client):
    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["simulate"] is False
    assert body["database_configured"] is True


def test_database_tables_are_created_on_first_use(client):
    # The client fixture already called init_db(); this asserts the model shape.
    session = database.get_session()
    try:
        columns = {column.name for column in database.AgriTelemetryLog.__table__.columns}
    finally:
        session.close()

    assert columns == {
        "id",
        "created_at",
        "source",
        "temperature_c",
        "humidity_pct",
        "soil_moisture_pct",
        "crop",
        "viability",
        "recommendation",
        "fusion_note",
    }
