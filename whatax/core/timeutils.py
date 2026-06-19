"""EVE-time helpers; all Whale Tax times are EVE time (UTC), always."""

import calendar
import datetime as dt


def eve_now() -> dt.datetime:
    """Current instant in EVE time (UTC), timezone-aware."""
    return dt.datetime.now(dt.timezone.utc)


def month_bounds(year: int, month: int) -> tuple[dt.datetime, dt.datetime]:
    """UTC half-open bounds ``[start, end)`` for a calendar month."""
    start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    if month == 12:
        end = dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc)
    else:
        end = dt.datetime(year, month + 1, 1, tzinfo=dt.timezone.utc)
    return start, end


def previous_month(reference: dt.datetime | None = None) -> tuple[int, int]:
    """``(year, month)`` of the month before ``reference`` (default: now)."""
    ref = reference or eve_now()
    year, month = ref.year, ref.month
    if month == 1:
        return year - 1, 12
    return year, month - 1


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]
