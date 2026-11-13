"""AgriCloud-CI backend: FastAPI application.

On Vercel this file is the Python serverless function and ``vercel.json`` routes
``/api/*`` to it. Locally it is started with
``uvicorn index:app --reload --port 8000`` from the ``api`` folder, which also
serves the three dashboard pages from ``public/``.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path
from typing import Optional

# Vercel runs this file with the project root as the working directory, so add
# the sibling backend modules (fusion.py, weather.py, ...) to the import path.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:  # Local development convenience; never required in production.
    from dotenv import load_dotenv

    load_dotenv(_HERE.parent / ".env")
except ImportError:  # pragma: no cover - python-dotenv ships in requirements.txt
    pass

from fastapi import Depends, FastAPI, Header, HTTPException, Query  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import assistant  # noqa: E402
import database  # noqa: E402
import decision_matrix  # noqa: E402
import fusion  # noqa: E402
import simulation  # noqa: E402
import weather  # noqa: E402
from config import load_settings  # noqa: E402
from utils import ensure_utc, to_iso  # noqa: E402

app = FastAPI(
    title="AgriCloud-CI",
    version="1.0.0",
    description=(
        "Edge-to-cloud precision microclimate advisory platform. An ESP32 node "
        "posts field readings, the cloud fuses them with Open-Meteo, grades crop "
        "viability and stores every decision in PostgreSQL."
    ),
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class TelemetryIn(BaseModel):
    """JSON body posted by the ESP32 firmware."""

    temp: float = Field(..., ge=-20, le=60, description="Air temperature in °C")
    humidity: float = Field(..., ge=0, le=100, description="Relative humidity in %")
    soil_moisture: float = Field(..., ge=0, le=100, description="Capacitive soil moisture in %")


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def require_device_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> None:
    """Reject requests that do not carry the shared device key.

    The key lives in the DEVICE_API_KEY environment variable and is compared in
    constant time so the endpoint does not leak timing information.
    """
    settings = load_settings()
    if not settings.device_api_key:
        raise HTTPException(
            status_code=503,
            detail="DEVICE_API_KEY is not configured on the server",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.device_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _device_from_row(row: database.AgriTelemetryLog) -> Optional[fusion.DeviceReading]:
    """Convert a stored ESP32 row into a fusion input (None if incomplete)."""
    if row.temperature_c is None or row.humidity_pct is None or row.soil_moisture_pct is None:
        return None
    return fusion.DeviceReading(
        temperature_c=float(row.temperature_c),
        humidity_pct=float(row.humidity_pct),
        soil_moisture_pct=float(row.soil_moisture_pct),
        recorded_at=ensure_utc(row.created_at),
    )


def _current_fusion(
    session: Session,
) -> tuple[fusion.FusedReading, Optional[weather.WeatherSnapshot]]:
    """Fuse the freshest ESP32 reading (or a simulated one) with Open-Meteo."""
    settings = load_settings()

    # Two rows are needed: the latest reading and the previous one, so the
    # spike rule can compare soil moisture between consecutive samples.
    readings = database.recent_device_readings(session, limit=2)
    device = _device_from_row(readings[0]) if readings else None
    previous = _device_from_row(readings[1]) if len(readings) > 1 else None

    if settings.simulate:
        # Demo mode: synthetic readings stand in for the hardware node.
        device = simulation.generate_reading()
        previous = None

    snapshot: Optional[weather.WeatherSnapshot] = None
    try:
        snapshot = weather.get_weather(settings.latitude, settings.longitude)
    except weather.WeatherError:
        # A fresh, valid device reading does not need the weather cross-check.
        # fusion.fuse() raises FusionError only when the fallback is required.
        snapshot = None

    return fusion.fuse(device, previous, snapshot), snapshot


def _weather_payload(snapshot: Optional[weather.WeatherSnapshot]) -> Optional[dict]:
    """JSON shape of the Open-Meteo cross-check shown on the dashboard."""
    if snapshot is None:
        return None
    return {
        "temperature_c": snapshot.temperature_c,
        "humidity_pct": snapshot.humidity_pct,
        "precipitation_mm": snapshot.precipitation_mm,
        "soil_moisture_pct": snapshot.soil_moisture_pct,
        "soil_moisture_estimated": snapshot.soil_moisture_estimated,
        "observed_at": to_iso(snapshot.observed_at),
        "cached": snapshot.is_cached,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    """Cheap readiness probe used by the dashboard and by run_checks.sh."""
    settings = load_settings()
    return {
        "status": "ok",
        "simulate": settings.simulate,
        "database_configured": bool(settings.database_url),
        "location": {"latitude": settings.latitude, "longitude": settings.longitude},
    }


@app.post("/api/telemetry", status_code=201, dependencies=[Depends(require_device_key)])
def post_telemetry(
    payload: TelemetryIn,
    session: Session = Depends(database.db_session),
) -> dict:
    """Store one validated ESP32 reading.

    Body ranges are enforced by Pydantic: temperature -20..60 °C, humidity and
    soil moisture 0..100 %. The reading becomes the primary fusion source for
    the next 15 minutes.
    """
    row = database.add_log(
        session,
        source=database.SOURCE_ESP32,
        temperature_c=payload.temp,
        humidity_pct=payload.humidity,
        soil_moisture_pct=payload.soil_moisture,
        fusion_note="ESP32 upload accepted; ranges validated at ingestion.",
    )
    return {
        "status": "saved",
        "id": row.id,
        "source": row.source,
        "received_at": to_iso(row.created_at),
    }


@app.get("/api/live")
def get_live(session: Session = Depends(database.db_session)) -> dict:
    """Return the latest fused reading and the source that was used."""
    settings = load_settings()
    try:
        fused, snapshot = _current_fusion(session)
    except fusion.FusionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "timestamp": to_iso(fused.timestamp),
        "source": fused.source,
        "fusion_note": fused.fusion_note,
        "temperature_c": fused.temperature_c,
        "humidity_pct": fused.humidity_pct,
        "soil_moisture_pct": fused.soil_moisture_pct,
        "weather": _weather_payload(snapshot),
        "location": {"latitude": settings.latitude, "longitude": settings.longitude},
        "simulate": settings.simulate,
    }


@app.get("/api/advisory")
def get_advisory(
    crop: str = Query(
        ..., min_length=1, description="Crop name from GET /api/crops, e.g. Wheat"
    ),
    session: Session = Depends(database.db_session),
) -> dict:
    """Evaluate the current conditions for one crop, save the verdict, return it."""
    settings = load_settings()
    # Validate the crop first: an unknown crop is a client error even when no
    # sensor or weather data is available yet.
    try:
        crop_name = decision_matrix.canonical_crop(crop)
    except decision_matrix.UnknownCropError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        fused, _snapshot = _current_fusion(session)
    except fusion.FusionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    evaluation = decision_matrix.evaluate(
        crop_name,
        fused.temperature_c,
        fused.humidity_pct,
        fused.soil_moisture_pct,
    )

    note = fused.fusion_note
    if settings.simulate:
        note += " Demo mode (SIMULATE=true): synthetic ESP32 values."

    row = database.add_log(
        session,
        source=fused.source,
        temperature_c=fused.temperature_c,
        humidity_pct=fused.humidity_pct,
        soil_moisture_pct=fused.soil_moisture_pct,
        crop=evaluation.crop,
        viability=evaluation.viability,
        recommendation=evaluation.recommendation,
        fusion_note=note,
    )

    return {
        "log_id": row.id,
        "crop": evaluation.crop,
        "viability": evaluation.viability,
        "recommendation": evaluation.recommendation,
        "metric_status": evaluation.metric_status,
        "conditions": {
            "temperature_c": fused.temperature_c,
            "humidity_pct": fused.humidity_pct,
            "soil_moisture_pct": fused.soil_moisture_pct,
        },
        "source": fused.source,
        "fusion_note": note,
        "evaluated_at": to_iso(fused.timestamp),
    }


class AssistantIn(BaseModel):
    """One chat turn from the dashboard assistant."""

    message: str = Field(..., min_length=1, max_length=500)
    crop: Optional[str] = Field(default=None, max_length=40)


@app.post("/api/assistant")
def ask_assistant(
    payload: AssistantIn,
    session: Session = Depends(database.db_session),
) -> dict:
    """Answer a farmer question from live telemetry, crop rules and history.

    The assistant is rule based on purpose: every answer is reproducible and can
    be traced to the fused reading or crops.json during the review.
    """
    crops = decision_matrix.load_crops()

    fused: Optional[fusion.FusedReading] = None
    snapshot: Optional[weather.WeatherSnapshot] = None
    try:
        fused, snapshot = _current_fusion(session)
    except fusion.FusionError:
        # Still answer range/system questions when live data is unavailable.
        fused = None

    recent = [row.as_dict() for row in database.list_logs(session, limit=5)]
    context = assistant.AssistantContext(
        fused=fused,
        crops=crops,
        weather=snapshot,
        recent_logs=recent,
        default_crop=payload.crop,
    )
    return assistant.reply(payload.message, context).as_dict()


@app.get("/api/crops")
def get_crops() -> dict:
    """Catalogue of supported crops and their bands (populates the dashboard)."""
    crops = decision_matrix.load_crops()
    return {
        "count": len(crops),
        "crops": [
            {
                "name": name,
                "temperature": rules["temperature"],
                "humidity": rules["humidity"],
                "soil_moisture": rules["soil_moisture"],
            }
            for name, rules in crops.items()
        ],
    }


@app.get("/api/history")
def get_history(
    crop: Optional[str] = Query(default=None, description="Optional crop filter"),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(database.db_session),
) -> dict:
    """Return audit rows newest first, optionally filtered by crop."""
    crop_filter = crop.strip() if crop and crop.strip() else None
    rows = database.list_logs(session, crop=crop_filter, limit=limit)
    return {
        "count": len(rows),
        "filter": {"crop": crop_filter, "limit": limit},
        "logs": [row.as_dict() for row in rows],
    }


# ---------------------------------------------------------------------------
# Static dashboard (local development only; Vercel serves public/ from its CDN)
# ---------------------------------------------------------------------------
PUBLIC_DIR = _HERE.parent / "public"
if PUBLIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="dashboard")
