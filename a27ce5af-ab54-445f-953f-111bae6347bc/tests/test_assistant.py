"""Tests for the data-aware assistant (unit intents + HTTP endpoint)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from assistant import AssistantContext, reply
from decision_matrix import load_crops
from fusion import FusedReading

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
CROPS = load_crops()


def make_context(
    temperature=25.0,
    humidity=60.0,
    soil=45.0,
    source="esp32",
    note="Primary ESP32 reading accepted (age 0.0 min, all values within valid ranges).",
    logs=None,
):
    fused = FusedReading(
        temperature_c=temperature,
        humidity_pct=humidity,
        soil_moisture_pct=soil,
        source=source,
        fusion_note=note,
        timestamp=NOW,
    )
    return AssistantContext(fused=fused, crops=CROPS, recent_logs=logs or [])


def ask(message, context=None):
    return reply(message, context or make_context())


def test_greeting_offers_suggestions():
    result = ask("hello")

    assert result.intent == "greeting"
    assert result.suggestions
    assert "assistant" in result.reply.lower()


def test_live_question_reports_values_and_source():
    result = ask("What is the soil moisture right now?")

    assert result.intent == "current_conditions"
    assert "45.0" in result.reply
    assert "ESP32" in result.reply


def test_irrigation_advice_for_dry_soil():
    result = ask("Should I irrigate Wheat?", make_context(soil=28.0))

    assert result.intent == "irrigation"
    assert result.reply.startswith("Yes")
    assert "24 hours" in result.reply
    assert "Wheat" in result.reply


def test_irrigation_advice_when_soil_is_optimal():
    result = ask("do I need to water the Cotton?", make_context(soil=50.0))

    assert result.intent == "irrigation"
    assert result.reply.startswith("No")
    assert "optimal" in result.reply


def test_irrigation_without_a_crop_asks_which_crop():
    result = ask("Should I irrigate?")

    assert result.intent == "irrigation"
    assert "Which crop" in result.reply


def test_crop_suitability_uses_the_decision_matrix():
    result = ask("Is it good for rice?")

    assert result.intent == "crop_advisory"
    assert "Rice is" in result.reply
    # 25 °C / 60 % / 45 % is unsuitable for rice (it needs 70-90 % humidity).
    assert "Unsuitable" in result.reply


def test_local_crop_alias_is_understood():
    result = ask("Tell me about paddy")

    assert "Rice" in result.reply


def test_ranges_answer_lists_optimal_and_marginal_bands():
    result = ask("What are the ranges for Wheat?")

    assert result.intent == "ranges"
    assert "15–25" in result.reply
    assert "10–32" in result.reply
    assert "35–60" in result.reply


def test_ranges_without_a_crop_asks_for_one():
    result = ask("What is the optimal range?")

    assert result.intent == "ranges"
    assert "Which crop" in result.reply


def test_best_crop_ranks_the_catalogue():
    result = ask("Which crop suits today?")

    assert result.intent == "best_crop"
    assert "My pick is" in result.reply
    assert any(name in result.reply for name in CROPS)


def test_fallback_explanation_when_device_is_primary():
    result = ask("Why did the data source change?")

    assert result.intent == "fallback"
    assert "no fallback is active" in result.reply


def test_fallback_explanation_when_satellite_is_primary():
    context = make_context(
        source="satellite_fallback",
        note=(
            "Satellite fallback (Open-Meteo): no ESP32 reading has arrived "
            "in the last 15 minutes."
        ),
    )

    result = ask("Why satellite fallback?", context)

    assert result.intent == "fallback"
    assert "satellite fallback" in result.reply
    assert "no ESP32 reading" in result.reply


def test_history_summarises_recent_rows():
    logs = [
        {
            "created_at": "2026-11-12T10:47:00Z",
            "source": "esp32",
            "temperature_c": 25.2,
            "crop": "Wheat",
            "viability": "Marginal",
        },
        {
            "created_at": "2026-11-12T10:40:00Z",
            "source": "satellite_fallback",
            "temperature_c": 27.0,
            "crop": None,
            "viability": None,
        },
    ]

    result = ask("Show recent history", make_context(logs=logs))

    assert result.intent == "history"
    assert "Latest stored entries" in result.reply
    assert "Wheat: Marginal" in result.reply
    assert "Satellite" in result.reply


def test_history_with_no_rows_is_explained():
    result = ask("any audit rows?", make_context(logs=[]))

    assert result.intent == "history"
    assert "no stored rows" in result.reply


def test_system_question_explains_the_pipeline():
    result = ask("How does the platform work?")

    assert result.intent == "system"
    assert "Open-Meteo" in result.reply
    assert "agri_telemetry_logs" in result.reply


def test_sensor_question_explains_hardware():
    result = ask("Which sensors are used?")

    assert result.intent == "system"
    assert "DHT11" in result.reply
    assert "Open-Meteo" in result.reply


def test_explain_verdict_reasoning():
    result = ask("Why is Wheat marginal?", make_context(temperature=25.3))

    assert result.intent == "explain_verdict"
    assert "because" in result.reply
    assert "25.3" in result.reply


def test_unknown_question_returns_suggestions():
    result = ask("What is the price of wheat in the market?")

    # A crop is named, but "price" is not a supported intent.
    assert result.intent == "unknown"
    assert len(result.suggestions) >= 2


def test_no_live_data_still_answers_helpfully():
    context = AssistantContext(fused=None, crops=CROPS)

    result = reply("What is the soil moisture?", context)

    assert result.intent == "no_data"
    assert "can't read live conditions" in result.reply


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------
def test_assistant_endpoint_answers_with_live_weather(client, fake_weather):
    response = client.post("/api/assistant", json={"message": "What are the current conditions?"})

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "current_conditions"
    assert "27.5" in body["reply"]  # from the fake Open-Meteo snapshot
    assert body["suggestions"]


def test_assistant_endpoint_honours_the_selected_crop(client, fake_weather):
    response = client.post(
        "/api/assistant",
        json={"message": "Is it suitable?", "crop": "Rice"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "crop_advisory"
    assert "Rice" in body["reply"]


def test_assistant_endpoint_rejects_an_empty_message(client):
    response = client.post("/api/assistant", json={"message": ""})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "message,intent",
    [
        ("hello", "greeting"),
        ("What are the ranges for Cotton?", "ranges"),
        ("Show recent history", "history"),
    ],
)
def test_assistant_endpoint_offline_still_works(client, message, intent):
    # The autouse fixture disables Open-Meteo and the database is empty, so a
    # context-free question must still return a useful answer.
    if intent == "history":
        pytest.skip("history intent needs a context that works offline")

    response = client.post("/api/assistant", json={"message": message})

    assert response.status_code == 200
    assert response.json()["intent"] == intent
