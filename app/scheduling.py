"""Maintenance-window logic. Pure functions, no DB/HTTP, so it is unit-testable."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
MAX_WINDOW = timedelta(hours=24)


class WindowError(ValueError):
    pass


def build_window(day: str, start: str, end: str, tz: ZoneInfo = LONDON) -> tuple[datetime, datetime]:
    """Parse 'YYYY-MM-DD', 'HH:MM', 'HH:MM' into tz-aware (start, end).

    An end time earlier than or equal to the start means the window crosses
    midnight (e.g. 23:00 -> 02:00 ends the next day).
    """
    try:
        d = date.fromisoformat(day)
        s = time.fromisoformat(start)
        e = time.fromisoformat(end)
    except ValueError as exc:
        raise WindowError("Invalid date or time") from exc
    if s == e:
        raise WindowError("Start and end time can't be the same")
    start_dt = datetime.combine(d, s, tzinfo=tz)
    end_day = d + timedelta(days=1) if e < s else d
    end_dt = datetime.combine(end_day, e, tzinfo=tz)
    for label, dt in (("Start", start_dt), ("End", end_dt)):
        if not _exists(dt):
            raise WindowError(f"{label} time {dt:%H:%M} doesn't exist on {dt:%d %b} in this timezone "
                              "(the clocks go forward). Pick a time outside the skipped hour.")
    # Ambiguous times (clocks go back, e.g. 01:30 happens twice) use the first occurrence (fold=0).
    if elapsed(start_dt, end_dt) <= timedelta(0):
        raise WindowError("End time must be after the start")
    if elapsed(start_dt, end_dt) > MAX_WINDOW:
        raise WindowError("A window can't be longer than 24 hours")
    return start_dt, end_dt


def _exists(dt: datetime) -> bool:
    """False for wall-clock times skipped by a DST jump: they don't survive a UTC round trip."""
    back = dt.astimezone(timezone.utc).astimezone(dt.tzinfo)
    return back.replace(tzinfo=None) == dt.replace(tzinfo=None)


def elapsed(start: datetime, end: datetime) -> timedelta:
    """Real elapsed time. Same-tzinfo aware subtraction in Python ignores DST, so go via UTC."""
    return end.astimezone(timezone.utc) - start.astimezone(timezone.utc)


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Half-open intervals: 02:00-05:00 and 05:00-06:00 do NOT clash."""
    return a_start < b_end and b_start < a_end


def window_state(start: datetime, end: datetime | None, status: str, now: datetime) -> str:
    """One of: completed, cancelled, in_progress, overdue, upcoming."""
    if status in ("completed", "cancelled"):
        return status
    end = end or start + timedelta(hours=1)
    if start <= now < end:
        return "in_progress"
    if now >= end:
        return "overdue"
    return "upcoming"


def humanise_duration(start: datetime, end: datetime) -> str:
    mins = int(elapsed(start, end).total_seconds() // 60)
    h, m = divmod(mins, 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


@dataclass
class WindowView:
    id: int
    description: str
    start: datetime
    end: datetime
    state: str
    duration: str
    crosses_midnight: bool
    # Same window in the viewer's timezone, only when it differs from the server's
    user_start: datetime | None = None
    user_end: datetime | None = None
