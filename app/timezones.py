# SPDX-License-Identifier: AGPL-3.0-or-later
"""Timezone helpers. Pure functions (no DB/HTTP) so they are unit-testable.

Model:
  * Every user has a display timezone (history, exports, "your time").
  * Every server/asset may have its own timezone. Maintenance windows are entered
    and shown in the server's local time, because "02:00-05:00" means the quiet
    hours where the server is, not where the admin happens to be.
  * Everything is stored in UTC (timestamptz). Timezones only affect input/display.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

DEFAULT_TZ = "Europe/London"
_REGIONS = ("Africa", "America", "Antarctica", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific")
COMMON = ["Europe/London", "UTC", "Europe/Dublin", "Europe/Paris", "Europe/Berlin", "Europe/Amsterdam",
          "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Toronto",
          "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland"]


@lru_cache(maxsize=1)
def all_zones() -> tuple[str, ...]:
    """Canonical region/city names only (drops legacy aliases like US/Eastern, Etc/GMT+5)."""
    zones = {z for z in available_timezones() if z.split("/", 1)[0] in _REGIONS and "/" in z}
    return tuple(sorted(zones | {"UTC"}))


def valid_tz(name: str | None) -> str | None:
    """Return the name if it is a zone we offer, else None. Whitelist, so no path tricks reach ZoneInfo."""
    name = (name or "").strip()
    return name if name in all_zones() else None


def get_tz(*names: str | None) -> ZoneInfo:
    """First valid name wins (e.g. asset tz, then user tz), falling back to DEFAULT_TZ."""
    for n in names:
        if v := valid_tz(n):
            return ZoneInfo(v)
    return ZoneInfo(DEFAULT_TZ)


def utc_offset(tz: ZoneInfo, at: datetime | None = None) -> str:
    off = (at or datetime.now(timezone.utc)).astimezone(tz).utcoffset()
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    h, m = divmod(abs(mins), 60)
    return f"UTC{sign}{h:02d}:{m:02d}"


def fmt(dt: datetime | None, tz: ZoneInfo, pattern: str = "%d %b %H:%M", show_zone: bool = True) -> str:
    """Format a stored (UTC) datetime in tz, e.g. '01 Jul 13:00 BST'."""
    if dt is None:
        return ""
    if dt.tzinfo is None:          # treat naive values as UTC rather than guessing local
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(tz)
    return f"{local.strftime(pattern)} {local.strftime('%Z')}" if show_zone else local.strftime(pattern)


def label(name: str, at: datetime | None = None) -> str:
    """'America/New_York' -> 'New York (America) · UTC-05:00'."""
    if name == "UTC":
        return "UTC · UTC+00:00"
    region, _, city = name.partition("/")
    return f"{city.replace('_', ' ').replace('/', ' / ')} ({region}) · {utc_offset(ZoneInfo(name), at)}"


def choices(at: datetime | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
    """Grouped (group, [(value, label)]) for a <select>, common zones first.
    Each zone appears exactly once, so a <select> can never have two 'selected' options."""
    groups = [("Common", [(z, label(z, at)) for z in COMMON if valid_tz(z)])]
    by_region: dict[str, list[tuple[str, str]]] = {}
    for z in all_zones():
        if z not in COMMON:
            by_region.setdefault(z.split("/", 1)[0], []).append((z, label(z, at)))
    groups += [(r, by_region[r]) for r in _REGIONS if r in by_region]
    return groups
