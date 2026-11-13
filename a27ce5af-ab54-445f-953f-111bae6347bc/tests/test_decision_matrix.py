"""Unit tests for the crop viability decision matrix (Stage 3).

The category tests are generated from ``crops.json`` itself, so every crop in
the catalogue (not just Wheat/Cotton/Soybeans) is covered automatically.
"""

from __future__ import annotations

import pytest

import decision_matrix
from decision_matrix import (
    MARGINAL,
    OPTIMAL,
    UNSUITABLE,
    UnknownCropError,
    evaluate,
    load_crops,
)

CROPS = load_crops()
METRICS = decision_matrix.METRICS


def optimal_values(rules: dict) -> dict:
    """Midpoint of every optimal band: guaranteed to grade as Optimal."""
    return {
        metric: (rules[metric]["optimal"][0] + rules[metric]["optimal"][1]) / 2
        for metric in METRICS
    }


def marginal_values(rules: dict) -> dict:
    """Push exactly one metric into its marginal band, keep the rest optimal."""
    values = optimal_values(rules)
    for metric in METRICS:
        optimal_low, optimal_high = rules[metric]["optimal"]
        marginal_low, marginal_high = rules[metric]["marginal"]
        if marginal_high > optimal_high:
            values[metric] = (optimal_high + marginal_high) / 2
            return values
        if marginal_low < optimal_low:
            values[metric] = (marginal_low + optimal_low) / 2
            return values
    raise AssertionError("no marginal gap found in the rule set")


def unsuitable_values(rules: dict) -> dict:
    """Dry the soil below the marginal band while keeping the rest optimal."""
    values = optimal_values(rules)
    values["soil_moisture"] = max(0.0, rules["soil_moisture"]["marginal"][0] - 5.0)
    return values


def test_catalogue_contains_the_original_three_plus_extras():
    assert {"Wheat", "Cotton", "Soybeans"} <= set(CROPS)
    assert len(CROPS) >= 10


@pytest.mark.parametrize("crop", sorted(CROPS))
def test_every_crop_can_be_optimal(crop):
    values = optimal_values(CROPS[crop])
    result = evaluate(crop, values["temperature"], values["humidity"], values["soil_moisture"])

    assert result.crop == crop
    assert result.viability == OPTIMAL
    assert crop in result.recommendation
    assert set(result.metric_status.values()) == {"optimal"}


@pytest.mark.parametrize("crop", sorted(CROPS))
def test_every_crop_can_be_marginal(crop):
    values = marginal_values(CROPS[crop])
    result = evaluate(crop, values["temperature"], values["humidity"], values["soil_moisture"])

    assert result.viability == MARGINAL
    statuses = list(result.metric_status.values())
    assert statuses.count("optimal") == 2
    assert any(status.startswith("marginal") for status in statuses)
    assert "Critical" not in result.recommendation


@pytest.mark.parametrize("crop", sorted(CROPS))
def test_every_crop_can_be_unsuitable(crop):
    values = unsuitable_values(CROPS[crop])
    result = evaluate(crop, values["temperature"], values["humidity"], values["soil_moisture"])

    assert result.viability == UNSUITABLE
    assert result.recommendation.startswith("Critical:")
    assert result.metric_status["soil_moisture"] == "unsuitable_low"


def test_marginal_humidity_is_detected_for_wheat():
    # 75 % is inside Wheat's marginal humidity band (30..80) but above optimal (70).
    result = evaluate("Wheat", 22.0, 75.0, 45.0)

    assert result.viability == MARGINAL
    assert result.metric_status["humidity"] == "marginal_high"
    assert "humidity" in result.recommendation.lower()


def test_low_soil_moisture_gives_an_irrigation_instruction():
    result = evaluate("Wheat", 22.0, 55.0, 28.0)

    assert result.viability == MARGINAL
    assert result.metric_status["soil_moisture"] == "marginal_low"
    assert "irrigate within 24 hours" in result.recommendation
    assert "28.0" in result.recommendation


def test_very_dry_soil_is_unsuitable_and_flagged_critical():
    result = evaluate("Soybeans", 25.0, 65.0, 20.0)

    assert result.viability == UNSUITABLE
    assert result.metric_status["soil_moisture"] == "unsuitable_low"
    assert "Critical:" in result.recommendation
    assert "irrigate within 24 hours" in result.recommendation


def test_new_crops_inherit_the_shared_advice_text():
    # Rice has no custom advice block, so it uses defaults.advice with {crop}.
    result = evaluate("Rice", 28.0, 80.0, 55.0)

    assert result.viability == MARGINAL
    assert "Rice" in result.recommendation
    assert "irrigate within 24 hours" in result.recommendation


def test_crop_name_lookup_is_case_insensitive():
    result = evaluate("wheat", 22.0, 55.0, 45.0)

    assert result.crop == "Wheat"


def test_unknown_crop_lists_the_supported_crops():
    with pytest.raises(UnknownCropError) as excinfo:
        evaluate("Coffee", 25.0, 60.0, 50.0)

    message = str(excinfo.value)
    assert "Wheat" in message
    assert "Tomato" in message


@pytest.mark.parametrize("crop", sorted(CROPS))
def test_rules_file_is_well_formed(crop):
    rules = CROPS[crop]

    for metric in METRICS:
        optimal_low, optimal_high = rules[metric]["optimal"]
        marginal_low, marginal_high = rules[metric]["marginal"]
        assert marginal_low <= optimal_low < optimal_high <= marginal_high

    for key in decision_matrix.ADVICE_KEYS:
        advice = rules["advice"][key]
        assert "{value}" in advice
        assert advice.strip()
