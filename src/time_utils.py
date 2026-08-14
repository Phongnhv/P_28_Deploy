"""Small time helpers that keep the current naive-UTC database contract stable."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current UTC time for legacy ``DateTime`` columns.

    The local SQLite models use timezone-naive ``DateTime`` fields.  Compute
    time from an aware UTC clock, then intentionally persist a naive UTC value
    until a dedicated timezone-aware schema migration is introduced.
    """
    return datetime.now(UTC).replace(tzinfo=None)
