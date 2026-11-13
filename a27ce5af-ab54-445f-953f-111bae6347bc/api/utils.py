"""Small helpers shared by the backend modules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    PostgreSQL returns aware timestamps, while SQLite (used by the test suite)
    returns naive ones. Treating naive values as UTC keeps every age and
    ordering comparison safe.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_iso(value: Optional[datetime]) -> Optional[str]:
    """Serialise a timestamp the way JavaScript's ``Date`` expects it."""
    if value is None:
        return None
    return ensure_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")
