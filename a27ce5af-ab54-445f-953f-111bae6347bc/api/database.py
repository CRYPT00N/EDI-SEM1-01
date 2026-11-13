"""Persistence layer for AgriCloud-CI.

One audit table, ``agri_telemetry_logs``, stores every sensor upload, fused
reading and advisory evaluation with a timestamp. The connection string always
comes from the ``DATABASE_URL`` environment variable.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Iterator, Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from utils import to_iso

SOURCE_ESP32 = "esp32"
SOURCE_SATELLITE = "satellite_fallback"


class Base(DeclarativeBase):
    """Declarative base for every ORM model in the project."""


class AgriTelemetryLog(Base):
    """One row per sensor upload, fused reading or advisory evaluation."""

    __tablename__ = "agri_telemetry_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    # 'esp32' for a direct device upload, 'satellite_fallback' for Open-Meteo.
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    temperature_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    soil_moisture_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    crop: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    viability: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    recommendation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fusion_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def as_dict(self) -> dict:
        """Plain dict used by the JSON endpoints (and easy to unit test)."""
        return {
            "id": self.id,
            "created_at": to_iso(self.created_at),
            "source": self.source,
            "temperature_c": self.temperature_c,
            "humidity_pct": self.humidity_pct,
            "soil_moisture_pct": self.soil_moisture_pct,
            "crop": self.crop,
            "viability": self.viability,
            "recommendation": self.recommendation,
            "fusion_note": self.fusion_note,
        }


# ---------------------------------------------------------------------------
# Engine and session management
# ---------------------------------------------------------------------------
# Vercel reuses a warm serverless instance, so the engine is cached per process.
# NullPool avoids holding sockets open in a short lived lambda invocation.
_lock = threading.RLock()
_engine: Optional[Engine] = None
_engine_url: Optional[str] = None
_session_factory: Optional[sessionmaker] = None
_tables_created = False


def _normalise_url(raw: str) -> str:
    """Accept the URL schemes that managed Postgres providers hand out."""
    if raw.startswith("postgres://"):
        return "postgresql+psycopg2://" + raw[len("postgres://") :]
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg2://" + raw[len("postgresql://") :]
    return raw


def get_database_url() -> str:
    """Return DATABASE_URL or fail with an actionable message."""
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and start Postgres "
            "with `docker compose up -d` (see README.md)."
        )
    return raw


def get_engine() -> Engine:
    """Create (once) and return the SQLAlchemy engine for DATABASE_URL."""
    global _engine, _engine_url, _session_factory, _tables_created

    url = _normalise_url(get_database_url())
    with _lock:
        if _engine is None or _engine_url != url:
            if _engine is not None:
                _engine.dispose()

            options: dict = {"future": True}
            if url.startswith("sqlite"):
                # SQLite is used only by the tests and for quick local demos.
                options["connect_args"] = {"check_same_thread": False}
                if ":memory:" in url:
                    options["poolclass"] = StaticPool
            else:
                options["poolclass"] = NullPool
                options["pool_pre_ping"] = True

            _engine = create_engine(url, **options)
            _engine_url = url
            _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
            _tables_created = False
        return _engine


def init_db() -> None:
    """Create the audit table if it does not exist yet (safe to call often)."""
    global _tables_created

    engine = get_engine()
    with _lock:
        if not _tables_created:
            Base.metadata.create_all(engine)
            _tables_created = True


def reset_database_state() -> None:
    """Forget cached connections. Used by the test suite between cases."""
    global _engine, _engine_url, _session_factory, _tables_created

    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _engine_url = None
        _session_factory = None
        _tables_created = False


def get_session() -> Session:
    """Open a session bound to the configured database."""
    init_db()
    with _lock:
        if _session_factory is None:  # pragma: no cover - defensive guard
            raise RuntimeError("Database session factory is not initialised")
        return _session_factory()


def db_session() -> Iterator[Session]:
    """FastAPI dependency: commit on success, roll back on errors, always close."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Small query helpers (kept here so the API layer stays focused on HTTP)
# ---------------------------------------------------------------------------
def add_log(
    session: Session,
    *,
    source: str,
    temperature_c: Optional[float] = None,
    humidity_pct: Optional[float] = None,
    soil_moisture_pct: Optional[float] = None,
    crop: Optional[str] = None,
    viability: Optional[str] = None,
    recommendation: Optional[str] = None,
    fusion_note: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> AgriTelemetryLog:
    """Insert one audit row and return it (flushed so ``id`` is available)."""
    row = AgriTelemetryLog(
        source=source,
        temperature_c=temperature_c,
        humidity_pct=humidity_pct,
        soil_moisture_pct=soil_moisture_pct,
        crop=crop,
        viability=viability,
        recommendation=recommendation,
        fusion_note=fusion_note,
    )
    if created_at is not None:
        row.created_at = created_at
    session.add(row)
    session.flush()
    return row


def recent_device_readings(session: Session, limit: int = 2) -> list[AgriTelemetryLog]:
    """Return the newest ESP32 uploads, newest first (used by fusion)."""
    statement = (
        select(AgriTelemetryLog)
        .where(AgriTelemetryLog.source == SOURCE_ESP32)
        .order_by(AgriTelemetryLog.created_at.desc(), AgriTelemetryLog.id.desc())
        .limit(limit)
    )
    return list(session.execute(statement).scalars().all())


def list_logs(
    session: Session, *, crop: Optional[str] = None, limit: int = 50
) -> list[AgriTelemetryLog]:
    """Return audit rows newest first, optionally filtered by crop."""
    statement = select(AgriTelemetryLog)
    if crop:
        statement = statement.where(func.lower(AgriTelemetryLog.crop) == crop.lower())
    statement = statement.order_by(
        AgriTelemetryLog.created_at.desc(), AgriTelemetryLog.id.desc()
    ).limit(limit)
    return list(session.execute(statement).scalars().all())
