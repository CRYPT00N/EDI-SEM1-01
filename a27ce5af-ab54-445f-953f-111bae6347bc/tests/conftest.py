"""Shared pytest fixtures.

The backend reads all of its configuration from environment variables, so the
test suite points ``DATABASE_URL`` at an in-memory SQLite database and injects a
throw-away device key *before* any application module is imported. That means
``pytest`` never needs a PostgreSQL server and never touches the network.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["DEVICE_API_KEY"] = "test-device-key"
os.environ["LATITUDE"] = "18.5204"
os.environ["LONGITUDE"] = "73.8567"
os.environ["SIMULATE"] = "false"

import database  # noqa: E402  (import after sys.path setup)
import weather  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    """A TestClient backed by a fresh in-memory database."""
    database.reset_database_state()
    import index  # imported here so the app picks up the test environment

    database.init_db()
    with TestClient(index.app) as test_client:
        yield test_client
    database.reset_database_state()


@pytest.fixture(autouse=True)
def offline_weather(monkeypatch):
    """Block real HTTP calls without hiding ``get_weather`` itself.

    Only the low-level fetch is patched, so the caching logic stays under test.
    Tests that need a snapshot ask for ``fake_weather`` instead.
    """

    def _raise(*_args, **_kwargs):
        raise weather.WeatherError("network disabled during tests")

    weather.clear_cache()
    monkeypatch.setattr(weather, "_fetch", _raise)
    yield
    weather.clear_cache()


@pytest.fixture()
def fake_weather(monkeypatch):
    """Install a deterministic Open-Meteo snapshot for the current test."""
    now = datetime.now(timezone.utc)
    snapshot = weather.WeatherSnapshot(
        temperature_c=27.5,
        humidity_pct=61.0,
        precipitation_mm=0.0,
        soil_moisture_pct=38.0,
        soil_moisture_estimated=False,
        observed_at=now,
        fetched_at=now,
    )
    monkeypatch.setattr(weather, "get_weather", lambda *_args, **_kwargs: snapshot)
    return snapshot


@pytest.fixture()
def insert_log():
    """Insert an audit row directly, bypassing the HTTP layer."""

    def _insert(**kwargs):
        session = database.get_session()
        try:
            row = database.add_log(session, **kwargs)
            session.commit()
            return row.id
        finally:
            session.close()

    return _insert
