"""AgriCloud assistant: a data-aware question answerer.

The assistant is deliberately deterministic: it reads the same fused telemetry
and crop rules as the dashboard, so every answer can be traced in a viva. It
needs no external API key, which keeps the project runnable offline.

Questions it understands (English, case-insensitive):
  * live conditions ("what is the soil moisture?")
  * crop suitability ("is it good for cotton?")
  * irrigation advice ("should I irrigate wheat?")
  * best crop for today's conditions
  * optimal/marginal bands for any crop
  * why the platform is on the satellite fallback
  * the latest audit rows
  * how the platform works
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import decision_matrix
from fusion import FusedReading
from utils import ensure_utc
from weather import WeatherSnapshot

# Local names farmers and reviewers may use instead of the catalogue names.
CROP_ALIASES = {
    "paddy": "Rice",
    "bajra": "Pearl Millet",
    "chana": "Chickpea",
    "gram": "Chickpea",
    "soya": "Soybeans",
    "soyabean": "Soybeans",
    "tomatoes": "Tomato",
    "wheat crop": "Wheat",
}

DEFAULT_SUGGESTIONS = [
    "What are the current conditions?",
    "Should I irrigate Wheat?",
    "Which crop suits today?",
]


@dataclass
class AssistantContext:
    """Everything the assistant may look at, assembled by the endpoint."""

    fused: Optional[FusedReading]
    crops: dict[str, dict]
    weather: Optional[WeatherSnapshot] = None
    recent_logs: list[dict] = field(default_factory=list)
    default_crop: Optional[str] = None


@dataclass
class AssistantReply:
    reply: str
    intent: str
    suggestions: list[str]

    def as_dict(self) -> dict:
        return {
            "reply": self.reply,
            "intent": self.intent,
            "suggestions": self.suggestions,
        }


def reply(message: str, context: AssistantContext) -> AssistantReply:
    """Answer one question using only platform data and crop rules."""
    text = " ".join((message or "").lower().split())
    crop = _find_crop(text, context.crops) or _canonical(context.default_crop, context.crops)

    if not text:
        return _help()
    if _is_greeting(text):
        return _greeting()
    if _is_thanks(text):
        return _thanks()
    if "help" in text or "what can you do" in text:
        return _help()
    if _is_about_system(text):
        return _system()

    # A named crop plus "why"/"explain" asks for the reasoning behind a verdict.
    if crop and ("why" in text or "explain" in text):
        return _explain_crop(crop, context)
    if crop and _is_about_crop(text):
        return _crop_advisory(crop, context)

    if _is_about_fallback(text):
        return _fallback(context)
    if _is_about_irrigation(text):
        return _irrigation(crop, context)
    if _is_about_best_crop(text):
        return _best_crop(context)
    if _is_about_ranges(text):
        return _ranges(crop, context)
    if _is_about_history(text):
        return _history(context)
    if _is_about_current(text):
        return _current(context)

    return _unknown()


# ---------------------------------------------------------------------------
# Intent helpers
# ---------------------------------------------------------------------------
def _is_greeting(text: str) -> bool:
    words = {"hi", "hello", "hey", "namaste", "hola", "yo"}
    return text in words or text.startswith(("hi ", "hello ", "hey "))


def _is_thanks(text: str) -> bool:
    return any(word in text for word in ("thank", "thanks", "dhanyavad", "great", "nice work"))


def _is_about_system(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "how does",
            "how do you",
            "what sensor",
            "which sensor",
            "dht11",
            "esp32",
            "decision matrix",
            "how it works",
            "architecture",
            "what is agricloud",
        )
    )


def _is_about_crop(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "suit",
            "good for",
            "good to grow",
            "can i grow",
            "should i plant",
            "advisory",
            "evaluate",
            "tell me about",
            "about the",
            "status of",
        )
    )


def _is_about_fallback(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "fallback",
            "satellite",
            "open-meteo",
            "open meteo",
            "grid data",
            "grid value",
            "fusion",
            "data source",
            "which source",
            "why did the source change",
        )
    )


def _is_about_irrigation(text: str) -> bool:
    return any(
        phrase in text
        for phrase in ("irrigat", "watering", "water the", "should i water", "dry soil")
    )


def _is_about_best_crop(text: str) -> bool:
    return any(
        phrase in text
        for phrase in ("which crop", "what crop", "best crop", "what should i plant", "suits today")
    )


def _is_about_ranges(text: str) -> bool:
    phrases = ("range", "optimal", "threshold", "band", "safe level", "limit")
    return any(phrase in text for phrase in phrases)


def _is_about_history(text: str) -> bool:
    return any(
        phrase in text
        for phrase in ("history", "recent", "past reading", "last reading", "audit", "log ")
    )


def _is_about_current(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "soil",
            "moisture",
            "temperature",
            "temp",
            "humidity",
            "reading",
            "current",
            "right now",
            "condition",
        )
    )


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------
def _greeting() -> AssistantReply:
    return AssistantReply(
        reply=(
            "Hi! I'm the AgriCloud assistant. I can read the live field node, grade any "
            "crop in the catalogue, explain the satellite fallback and summarise the "
            "stored history. What would you like to know?"
        ),
        intent="greeting",
        suggestions=DEFAULT_SUGGESTIONS,
    )


def _help() -> AssistantReply:
    return AssistantReply(
        reply=(
            "I answer from live platform data: current soil moisture, temperature and "
            "humidity; whether a crop suits today's conditions; irrigation timing; "
            "optimal and marginal bands; why the platform switched to Open-Meteo; and "
            "the latest audit rows. Try one of the suggestions below."
        ),
        intent="help",
        suggestions=DEFAULT_SUGGESTIONS,
    )


def _thanks() -> AssistantReply:
    return AssistantReply(
        reply="You're welcome. Ask me again whenever the field conditions change.",
        intent="thanks",
        suggestions=DEFAULT_SUGGESTIONS,
    )


def _system() -> AssistantReply:
    return AssistantReply(
        reply=(
            "The ESP32 reads a capacitive soil probe and a DHT11, averages five samples "
            "and posts them over HTTPS every five minutes. The cloud fuses that reading "
            "with Open-Meteo (15 minute freshness, range and 40 pp spike checks), grades "
            "the crop against crops.json, saves the verdict in agri_telemetry_logs and "
            "serves this dashboard."
        ),
        intent="system",
        suggestions=[
            "Which sensors are used?",
            "What is the soil moisture?",
            "Show recent history",
        ],
    )


def _current(context: AssistantContext) -> AssistantReply:
    fused = context.fused
    if fused is None:
        return _no_data()
    weather = context.weather
    source_label = (
        "ESP32 edge node" if fused.source == "esp32" else "Open-Meteo satellite fallback"
    )
    lines = [
        f"Right now: air temperature {fused.temperature_c:.1f} °C, humidity "
        f"{fused.humidity_pct:.1f} % and soil moisture {fused.soil_moisture_pct:.1f} %.",
        f"Source: {source_label}.",
    ]
    if weather is not None:
        lines.append(
            f"The Open-Meteo grid is at {weather.temperature_c:.1f} °C, "
            f"{weather.humidity_pct:.1f} % humidity and "
            f"{weather.soil_moisture_pct:.1f} % soil moisture."
        )
    lines.append(f"Fusion note: {fused.fusion_note}")
    return AssistantReply(
        reply=" ".join(lines),
        intent="current_conditions",
        suggestions=DEFAULT_SUGGESTIONS,
    )


def _irrigation(crop: Optional[str], context: AssistantContext) -> AssistantReply:
    fused = context.fused
    if fused is None:
        return _no_data()
    if crop is None:
        names = ", ".join(list(context.crops)[:5])
        return AssistantReply(
            reply=(
                "Which crop are you asking about? I know the bands for "
                f"{names} and more. For example: 'Should I irrigate Wheat?'"
            ),
            intent="irrigation",
            suggestions=[
                "Should I irrigate Wheat?",
                "Should I irrigate Rice?",
                "Which crop suits today?",
            ],
        )

    evaluation = decision_matrix.evaluate(
        crop, fused.temperature_c, fused.humidity_pct, fused.soil_moisture_pct
    )
    rules = context.crops[evaluation.crop]["soil_moisture"]
    status = evaluation.metric_status["soil_moisture"]
    low, high = rules["optimal"]

    if status in ("marginal_low", "unsuitable_low"):
        answer = (
            f"Yes. Soil moisture is {fused.soil_moisture_pct:.1f} %, below the optimal "
            f"{low:.0f}–{high:.0f} % band for {evaluation.crop}, so irrigation is advised "
            "within 24 hours."
        )
    elif status in ("marginal_high", "unsuitable_high"):
        answer = (
            f"No. Soil moisture is {fused.soil_moisture_pct:.1f} %, above the optimal "
            f"{low:.0f}–{high:.0f} % band for {evaluation.crop}; "
            "hold irrigation and check drainage."
        )
    else:
        answer = (
            f"No. Soil moisture is {fused.soil_moisture_pct:.1f} %, inside the optimal "
            f"{low:.0f}–{high:.0f} % band for {evaluation.crop}, "
            "so no irrigation is needed right now."
        )

    return AssistantReply(
        reply=answer + " " + evaluation.recommendation,
        intent="irrigation",
        suggestions=[
            f"Why is {evaluation.crop} {evaluation.viability.lower()}?",
            "Which crop suits today?",
            "Show recent history",
        ],
    )


def _crop_advisory(crop: str, context: AssistantContext) -> AssistantReply:
    fused = context.fused
    if fused is None:
        return _no_data()

    evaluation = decision_matrix.evaluate(
        crop, fused.temperature_c, fused.humidity_pct, fused.soil_moisture_pct
    )
    source = "ESP32 edge node" if fused.source == "esp32" else "Open-Meteo fallback"
    return AssistantReply(
        reply=(
            f"{evaluation.crop} is {evaluation.viability} today with {fused.temperature_c:.1f} °C, "
            f"{fused.humidity_pct:.1f} % humidity and "
            f"{fused.soil_moisture_pct:.1f} % soil moisture "
            f"(source: {source}). {evaluation.recommendation}"
        ),
        intent="crop_advisory",
        suggestions=[
            f"Why is {evaluation.crop} {evaluation.viability.lower()}?",
            "Which crop suits today?",
            "Show recent history",
        ],
    )


def _explain_crop(crop: str, context: AssistantContext) -> AssistantReply:
    fused = context.fused
    if fused is None:
        return _no_data()

    evaluation = decision_matrix.evaluate(
        crop, fused.temperature_c, fused.humidity_pct, fused.soil_moisture_pct
    )
    rules = context.crops[evaluation.crop]
    values = {
        "temperature": fused.temperature_c,
        "humidity": fused.humidity_pct,
        "soil_moisture": fused.soil_moisture_pct,
    }
    units = {"temperature": "°C", "humidity": "%", "soil_moisture": "%"}
    labels = {
        "temperature": "temperature",
        "humidity": "humidity",
        "soil_moisture": "soil moisture",
    }

    parts = []
    for metric, value in values.items():
        status = evaluation.metric_status[metric]
        low, high = rules[metric]["optimal"]
        parts.append(
            f"{labels[metric]} {value:.1f} {units[metric]} is {_describe(status)} "
            f"the optimal {low:.0f}–{high:.0f} {units[metric]} band"
        )

    return AssistantReply(
        reply=(
            f"{evaluation.crop} is {evaluation.viability} because " + "; ".join(parts) + "."
        ),
        intent="explain_verdict",
        suggestions=[
            f"Should I irrigate {evaluation.crop}?",
            "Which crop suits today?",
            f"What are the ranges for {evaluation.crop}?",
        ],
    )


def _best_crop(context: AssistantContext) -> AssistantReply:
    fused = context.fused
    if fused is None:
        return _no_data()

    ranked = []
    for name in context.crops:
        evaluation = decision_matrix.evaluate(
            name, fused.temperature_c, fused.humidity_pct, fused.soil_moisture_pct
        )
        ranked.append(evaluation)

    order = {"Optimal": 0, "Marginal": 1, "Unsuitable": 2}
    ranked.sort(key=lambda evaluation: order[evaluation.viability])
    best = ranked[0]

    best_names = [
        f"{item.crop} ({item.viability})" for item in ranked if item.viability == "Optimal"
    ]
    close_names = [item.crop for item in ranked if item.viability == "Marginal"][:3]

    if best_names:
        summary = "Optimal right now: " + ", ".join(best_names[:4]) + "."
        if close_names:
            summary += " Also marginal: " + ", ".join(close_names) + "."
    elif close_names:
        summary = (
            "No crop is in its optimal band right now. Closest matches: "
            + ", ".join(close_names)
            + "."
        )
    else:
        summary = "No catalogue crop suits the current conditions."

    return AssistantReply(
        reply=(
            f"At {fused.temperature_c:.1f} °C, {fused.humidity_pct:.1f} % humidity and "
            f"{fused.soil_moisture_pct:.1f} % soil moisture. {summary} "
            f"My pick is {best.crop} ({best.viability})."
        ),
        intent="best_crop",
        suggestions=[
            f"Tell me about {best.crop}",
            f"Why is {best.crop} {best.viability.lower()}?",
            "Show recent history",
        ],
    )


def _ranges(crop: Optional[str], context: AssistantContext) -> AssistantReply:
    if crop is None:
        names = ", ".join(list(context.crops)[:5])
        return AssistantReply(
            reply=(
                "Which crop's ranges do you need? I have "
                f"{names} and more. For example: 'What are the ranges for Cotton?'"
            ),
            intent="ranges",
            suggestions=["What are the ranges for Wheat?", "What are the ranges for Rice?"],
        )

    rules = context.crops[crop]
    temperature = rules["temperature"]
    humidity = rules["humidity"]
    soil = rules["soil_moisture"]

    return AssistantReply(
        reply=(
            f"{crop} bands — temperature: optimal {temperature['optimal'][0]:.0f}–"
            f"{temperature['optimal'][1]:.0f} °C, marginal {temperature['marginal'][0]:.0f}–"
            f"{temperature['marginal'][1]:.0f} °C. Humidity: optimal "
            f"{humidity['optimal'][0]:.0f}–{humidity['optimal'][1]:.0f} %, marginal "
            f"{humidity['marginal'][0]:.0f}–{humidity['marginal'][1]:.0f} %. Soil moisture: "
            f"optimal {soil['optimal'][0]:.0f}–{soil['optimal'][1]:.0f} %, marginal "
            f"{soil['marginal'][0]:.0f}–{soil['marginal'][1]:.0f} %."
        ),
        intent="ranges",
        suggestions=[
            f"Tell me about {crop}",
            f"Should I irrigate {crop}?",
            "Which crop suits today?",
        ],
    )


def _fallback(context: AssistantContext) -> AssistantReply:
    fused = context.fused
    if fused is None:
        return AssistantReply(
            reply=(
                "Neither source is available right now: there is no trusted ESP32 reading "
                "and Open-Meteo could not be reached. The dashboard will recover on the "
                "next refresh."
            ),
            intent="fallback",
            suggestions=DEFAULT_SUGGESTIONS,
        )

    if fused.source == "esp32":
        return AssistantReply(
            reply=(
                "The platform is using the ESP32 edge node, so no fallback is active. "
                f"Rule check: {fused.fusion_note} The cloud switches to Open-Meteo when "
                "the device is silent for 15 minutes, a value leaves its valid range, or "
                "soil moisture jumps more than 40 percentage points."
            ),
            intent="fallback",
            suggestions=["What are the current conditions?", "Show recent history"],
        )

    grid = ""
    if context.weather is not None:
        grid = (
            f" Grid values: {context.weather.temperature_c:.1f} °C, "
            f"{context.weather.humidity_pct:.1f} % humidity, "
            f"{context.weather.soil_moisture_pct:.1f} % soil moisture."
        )
    return AssistantReply(
        reply=f"The platform is on the satellite fallback.{grid} Reason: {fused.fusion_note}",
        intent="fallback",
        suggestions=[
            "What are the current conditions?",
            "Which crop suits today?",
            "What is the soil moisture?",
        ],
    )


def _history(context: AssistantContext) -> AssistantReply:
    logs = context.recent_logs
    if not logs:
        return AssistantReply(
            reply=(
                "There are no stored rows yet. Once the ESP32 posts a reading or someone "
                "runs an advisory, I can summarise the audit trail here."
            ),
            intent="history",
            suggestions=DEFAULT_SUGGESTIONS,
        )

    lines = []
    for row in logs[:5]:
        stamp = _short_time(row.get("created_at"))
        source = "Satellite" if row.get("source") == "satellite_fallback" else "ESP32"
        crop = row.get("crop")
        if crop:
            detail = f"{crop}: {row.get('viability')}"
        else:
            detail = "sensor upload"
        temperature = row.get("temperature_c")
        reading = f"{temperature:.1f} °C" if isinstance(temperature, (int, float)) else "—"
        lines.append(f"• {stamp} · {source} · {reading} · {detail}")

    return AssistantReply(
        reply="Latest stored entries:\n" + "\n".join(lines),
        intent="history",
        suggestions=DEFAULT_SUGGESTIONS,
    )


def _no_data() -> AssistantReply:
    return AssistantReply(
        reply=(
            "I can't read live conditions right now: there is no trusted ESP32 reading "
            "and Open-Meteo is unavailable. I can still explain the crop ranges or how "
            "the fusion works."
        ),
        intent="no_data",
        suggestions=[
            "What are the ranges for Wheat?",
            "How does the fusion work?",
            "What sensors are used?",
        ],
    )


def _unknown() -> AssistantReply:
    return AssistantReply(
        reply=(
            "I don't have an answer for that yet. I can help with live conditions, crop "
            "suitability, irrigation timing, crop ranges, the satellite fallback and the "
            "stored history. Try one of the suggestions below."
        ),
        intent="unknown",
        suggestions=DEFAULT_SUGGESTIONS,
    )


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
def _find_crop(text: str, crops: dict[str, dict]) -> Optional[str]:
    """Find a catalogue crop (or a local alias) mentioned in the question."""
    for name in crops:
        if name.lower() in text:
            return name
    for alias, name in CROP_ALIASES.items():
        if alias in text and name in crops:
            return name
    return None


def _canonical(crop: Optional[str], crops: dict[str, dict]) -> Optional[str]:
    if not crop:
        return None
    try:
        return decision_matrix.canonical_crop(crop)
    except decision_matrix.UnknownCropError:
        return _find_crop(crop.lower(), crops)


def _describe(status: str) -> str:
    return {
        "optimal": "inside",
        "marginal_low": "slightly below",
        "marginal_high": "slightly above",
        "unsuitable_low": "well below",
        "unsuitable_high": "well above",
    }.get(status, "outside")


def _short_time(value: Optional[str]) -> str:
    if not value:
        return "—"
    try:
        moment = ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return value
    return moment.strftime("%d %b %H:%M")
