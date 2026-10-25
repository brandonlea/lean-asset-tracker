from datetime import datetime, timedelta

import pytest

from app.scheduling import LONDON, WindowError, build_window, humanise_duration, overlaps, window_state


def test_basic_window_0200_to_0500():
    s, e = build_window("2026-11-03", "02:00", "05:00")
    assert (s.hour, e.hour) == (2, 5)
    assert e - s == timedelta(hours=3)
    assert humanise_duration(s, e) == "3h"


def test_crosses_midnight():
    s, e = build_window("2026-11-03", "23:30", "01:00")
    assert e.date().isoformat() == "2026-11-04"
    assert humanise_duration(s, e) == "1h 30m"


def test_same_start_end_rejected():
    with pytest.raises(WindowError):
        build_window("2026-11-03", "02:00", "02:00")


def test_garbage_rejected():
    with pytest.raises(WindowError):
        build_window("2026-13-40", "02:00", "05:00")


def test_overlap_rules():
    a = build_window("2026-11-03", "02:00", "05:00")
    assert overlaps(*a, *build_window("2026-11-03", "04:00", "06:00"))
    assert not overlaps(*a, *build_window("2026-11-03", "05:00", "06:00"))  # back-to-back ok
    assert overlaps(*a, *build_window("2026-11-02", "23:00", "03:00"))      # overnight clash


def test_states():
    s, e = build_window("2026-11-03", "02:00", "05:00")
    assert window_state(s, e, "scheduled", s - timedelta(minutes=1)) == "upcoming"
    assert window_state(s, e, "scheduled", s + timedelta(hours=1)) == "in_progress"
    assert window_state(s, e, "scheduled", e) == "overdue"
    assert window_state(s, e, "completed", e) == "completed"


def test_bst_to_gmt_change_is_handled():
    # UK clocks go back 01:00 on 2026-10-25; an overnight window still has real duration
    s, e = build_window("2026-10-24", "23:00", "03:00")
    assert s.tzinfo is LONDON
    # Wall clock says 4h, but the clocks went back so 5 real hours pass
    assert humanise_duration(s, e) == "5h"
