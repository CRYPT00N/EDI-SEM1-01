"""Crop viability decision matrix.

All agronomic rules live in ``crops.json`` so they can be tuned without touching
Python. ``evaluate()`` grades the three microclimate values for one crop:

* ``Optimal``    - all three values are inside their optimal band,
* ``Marginal``   - all three values are inside their marginal band (but not all
                   of them are optimal),
* ``Unsuitable`` - at least one value falls outside its marginal band.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CROPS_FILE = Path(__file__).with_name("crops.json")

OPTIMAL = "Optimal"
MARGINAL = "Marginal"
UNSUITABLE = "Unsuitable"

METRICS = ("temperature", "humidity", "soil_moisture")
ADVICE_KEYS = tuple(
    f"{metric}_{direction}" for metric in METRICS for direction in ("low", "high")
)


class UnknownCropError(ValueError):
    """Raised when a crop is not present in crops.json."""


@dataclass(frozen=True)
class Evaluation:
    """Verdict for one crop plus the trace needed to explain it in a viva."""

    crop: str
    viability: str
    recommendation: str
    metric_status: dict[str, str]


def load_crops() -> dict[str, dict]:
    """Read and validate crops.json. Read on every call so edits apply instantly.

    Advice is merged from ``defaults.advice`` and the optional per-crop
    ``advice`` block, so a new crop only needs its three range bands.
    """
    try:
        with CROPS_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Crop rule file not found: {CROPS_FILE}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{CROPS_FILE.name} is not valid JSON: {exc}") from exc

    crops = data.get("crops")
    if not isinstance(crops, dict) or not crops:
        raise RuntimeError(f"{CROPS_FILE.name} must contain a non-empty 'crops' object")

    shared_advice = (data.get("defaults") or {}).get("advice") or {}
    if not isinstance(shared_advice, dict):
        raise RuntimeError(f"{CROPS_FILE.name}: 'defaults.advice' must be an object")

    merged: dict[str, dict] = {}
    for name, rules in crops.items():
        advice = dict(shared_advice)
        advice.update(rules.get("advice") or {})
        crop_rules = {**rules, "advice": advice}
        _validate_rules(name, crop_rules)
        merged[name] = crop_rules
    return merged


def _validate_rules(name: str, rules: dict) -> None:
    """Fail loudly on a malformed rule file instead of returning a wrong grade."""
    for metric in METRICS:
        rule = rules.get(metric)
        if not isinstance(rule, dict):
            raise RuntimeError(f"{name}: missing the '{metric}' rule")

        bounds = {}
        for band in ("optimal", "marginal"):
            pair = rule.get(band)
            if not isinstance(pair, list) or len(pair) != 2:
                raise RuntimeError(f"{name}.{metric}.{band} must be [low, high]")
            low, high = pair
            if low >= high:
                raise RuntimeError(f"{name}.{metric}.{band} low must be < high")
            bounds[band] = (low, high)

        if bounds["marginal"][0] > bounds["optimal"][0]:
            raise RuntimeError(f"{name}.{metric}: marginal low must be <= optimal low")
        if bounds["marginal"][1] < bounds["optimal"][1]:
            raise RuntimeError(f"{name}.{metric}: marginal high must be >= optimal high")

    advice = rules.get("advice")
    if not isinstance(advice, dict):
        raise RuntimeError(f"{name}: missing the 'advice' object")
    for key in ADVICE_KEYS:
        if key not in advice:
            raise RuntimeError(f"{name}: advice is missing '{key}'")


def canonical_crop(crop: str) -> str:
    """Return the crop name exactly as stored in crops.json.

    The endpoints validate the crop name before touching the weather or sensor
    data, so an unsupported crop is a 400 rather than a confusing 503.
    """
    return _canonical_crop(crop, load_crops())


def evaluate(
    crop: str, temperature_c: float, humidity_pct: float, soil_moisture_pct: float
) -> Evaluation:
    """Grade the conditions for one crop and build a plain-English recommendation."""
    crops = load_crops()
    name = _canonical_crop(crop, crops)
    rules = crops[name]

    values = {
        "temperature": float(temperature_c),
        "humidity": float(humidity_pct),
        "soil_moisture": float(soil_moisture_pct),
    }
    statuses = {
        metric: _classify(values[metric], rules[metric]) for metric in METRICS
    }

    if all(status == "optimal" for status in statuses.values()):
        viability = OPTIMAL
    elif all(status.startswith(("optimal", "marginal")) for status in statuses.values()):
        viability = MARGINAL
    else:
        viability = UNSUITABLE

    return Evaluation(
        crop=name,
        viability=viability,
        recommendation=_recommendation(name, values, statuses, rules["advice"]),
        metric_status=statuses,
    )


def _canonical_crop(crop: str, crops: dict[str, dict]) -> str:
    """Match the requested crop case-insensitively and return its stored name."""
    requested = (crop or "").strip()
    for name in crops:
        if name.lower() == requested.lower():
            return name
    supported = ", ".join(crops)
    raise UnknownCropError(
        f"Unknown crop '{requested}'. Supported crops: {supported}."
    )


def _classify(value: float, rule: dict) -> str:
    """Return one of optimal / marginal_low / marginal_high / unsuitable_low / _high."""
    optimal_low, optimal_high = rule["optimal"]
    marginal_low, marginal_high = rule["marginal"]

    if optimal_low <= value <= optimal_high:
        return "optimal"
    if marginal_low <= value <= marginal_high:
        return "marginal_low" if value < optimal_low else "marginal_high"
    return "unsuitable_low" if value < marginal_low else "unsuitable_high"


def _recommendation(
    crop: str, values: dict[str, float], statuses: dict[str, str], advice: dict
) -> str:
    """Build the farmer-facing sentence(s) for the current verdict."""
    if all(status == "optimal" for status in statuses.values()):
        return (
            f"All three readings are inside the optimal range for {crop}; keep the "
            "current irrigation schedule and continue monitoring."
        )

    sentences = []
    for metric in METRICS:
        status = statuses[metric]
        if status == "optimal":
            continue
        direction = "low" if status.endswith("low") else "high"
        template = advice[f"{metric}_{direction}"]
        sentence = template.format(value=f"{values[metric]:.1f}", crop=crop)
        if status.startswith("unsuitable"):
            # Out of the marginal band: say it clearly, and say it first.
            sentence = "Critical: " + sentence
        sentences.append(sentence)

    return " ".join(sentences)
