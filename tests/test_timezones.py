from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import timezones as tzs
from app.scheduling import WindowError, build_window, elapsed

UTC = timezone.utc
LON, NY, SYD = ZoneInfo("Europe/London"), ZoneInfo("America/New_York"), ZoneInfo("Australia/Sydney")


def test_whitelist_rejects_junk_and_aliases():
    for bad in ("", None, "Not/AZone", "../../etc/passwd", "US/Eastern", "Etc/GMT+5", "europe/london"):
        assert tzs.valid_tz(bad) is None
    for good in ("UTC", "Europe/London", "America/New_York", "Asia/Kolkata", " Europe/Berlin "):
        assert tzs.valid_tz(good) == good.strip()


def test_get_tz_precedence_and_fallback():
    assert tzs.get_tz("America/New_York", "Europe/Paris").key == "America/New_York"   # server tz wins
    assert tzs.get_tz(None, "Europe/Paris").key == "Europe/Paris"                      # else owner's
    assert tzs.get_tz("garbage", None).key == tzs.DEFAULT_TZ                           # else default


def test_choices_have_each_zone_once():
    flat = [v for _, opts in tzs.choices() for v, _ in opts]
    assert len(flat) == len(set(flat)) == len(tzs.all_zones())
    assert flat[0] == "Europe/London"


def test_fmt_summer_shows_bst_not_utc():
    # The bug class this release fixes: in winter London == UTC, so only a summer value proves it.
    summer = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert tzs.fmt(summer, LON) == "01 Jul 13:00 BST"
    assert tzs.fmt(summer, NY) == "01 Jul 08:00 EDT"
    assert tzs.fmt(datetime(2026, 12, 1, 12, 0, tzinfo=UTC), LON) == "01 Dec 12:00 GMT"
    assert tzs.fmt(datetime(2026, 7, 1, 12, 0), LON) == "01 Jul 13:00 BST"           # naive treated as UTC
    assert tzs.fmt(None, LON) == ""


def test_utc_offset_label():
    assert tzs.utc_offset(ZoneInfo("Asia/Kolkata")) == "UTC+05:30"
    assert tzs.utc_offset(NY, datetime(2026, 1, 1, tzinfo=UTC)) == "UTC-05:00"
    assert tzs.label("America/New_York", datetime(2026, 1, 1, tzinfo=UTC)) == "New York (America) · UTC-05:00"


def test_window_in_server_timezone_is_stored_as_correct_utc():
    s, e = build_window("2026-12-10", "02:00", "05:00", NY)          # EST, UTC-5
    assert s.astimezone(UTC) == datetime(2026, 12, 10, 7, 0, tzinfo=UTC)
    assert e.astimezone(UTC) == datetime(2026, 12, 10, 10, 0, tzinfo=UTC)
    s, e = build_window("2026-07-10", "02:00", "05:00", NY)          # EDT, UTC-4
    assert s.astimezone(UTC) == datetime(2026, 7, 10, 6, 0, tzinfo=UTC)
    # Same window seen from London: 07:00-10:00 GMT in winter
    s, e = build_window("2026-12-10", "02:00", "05:00", NY)
    assert (s.astimezone(LON).strftime("%H:%M %Z"), e.astimezone(LON).strftime("%H:%M %Z")) == ("07:00 GMT", "10:00 GMT")


def test_window_viewed_from_other_side_of_world_changes_day():
    s, e = build_window("2026-12-10", "22:00", "23:00", LON)
    local = s.astimezone(SYD)                                         # AEDT, UTC+11
    assert (local.day, local.strftime("%H:%M")) == (11, "09:00")


def test_skipped_hour_is_refused():
    # 29 Mar 2026, London clocks jump 01:00 -> 02:00, so 01:30 never happens
    with pytest.raises(WindowError, match="doesn't exist"):
        build_window("2026-03-29", "01:30", "03:00", LON)
    # New York jumps 02:00 -> 03:00 on 8 Mar 2026: the classic "02:00-05:00" overnight preset
    with pytest.raises(WindowError, match="doesn't exist"):
        build_window("2026-03-08", "02:00", "05:00", NY)
    s, e = build_window("2026-03-08", "03:00", "05:00", NY)           # just after the jump is fine
    assert elapsed(s, e) == timedelta(hours=2)


def test_repeated_hour_counts_real_duration():
    # 25 Oct 2026 London clocks go back 02:00 -> 01:00: 00:00-03:00 is 4 real hours
    s, e = build_window("2026-10-25", "00:00", "03:00", LON)
    assert elapsed(s, e) == timedelta(hours=4)
    s, _ = build_window("2026-10-25", "01:30", "02:30", LON)          # ambiguous: first (BST) occurrence
    assert s.astimezone(UTC) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
