"""EVE-time helpers (TECHNICAL.md §9, §16).

**All Whale Tax times are EVE time (= UTC), always.** Every period boundary,
``emitted_at``, ``due_date``, and the staff edit window is computed here, never
from the host's local tz and never from a configurable setting. Centralizing the
math in one module is what keeps calc and UI from drifting.
"""

import calendar
import datetime as dt


def eve_now() -> dt.datetime:
    """Current instant in EVE time (UTC), timezone-aware."""
    return dt.datetime.now(dt.timezone.utc)


def month_bounds(year: int, month: int) -> tuple[dt.datetime, dt.datetime]:
    """UTC half-open bounds ``[start, end)`` for a calendar month.

    ``start`` is the first instant of ``year``/``month``; ``end`` is the first
    instant of the following month. A row belongs to the period iff
    ``start <= ts < end``.
    """
    start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    if month == 12:
        end = dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc)
    else:
        end = dt.datetime(year, month + 1, 1, tzinfo=dt.timezone.utc)
    return start, end


def previous_month(reference: dt.datetime | None = None) -> tuple[int, int]:
    """``(year, month)`` of the month *before* ``reference`` (default: now).

    Used by the 1st-of-month run, which bills the previous month (§9/§13).
    """
    ref = reference or eve_now()
    year, month = ref.year, ref.month
    if month == 1:
        return year - 1, 12
    return year, month - 1


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]
