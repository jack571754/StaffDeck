"""Common database utility functions shared across model modules."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


def utc_now() -> datetime:
    """Return current UTC time with tzinfo stripped (naive UTC datetime)."""
    return datetime.now(UTC).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    """Generate a new string ID with the given prefix (prefix_ + 16 hex chars)."""
    return f"{prefix}_{uuid4().hex[:16]}"
