"""Alert rules.

The bar these tests defend: a monitor nobody mutes. Most of them are about
*not* alerting — false positives are the failure mode that kills the feature,
because a muted alert channel is worse than no alert channel.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app import alerts as al
from app.alerts import Alert, AssetView

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def asset(**kw):
    base = dict(id=1, name="nas-01")
    base.update(kw)
    return AssetView(**base)


def metrics(disk=None, mem=None):
    out = {}
    if disk:
        out["disk"] = {"used": disk[0], "total": disk[1]}
    if mem:
        out["memory"] = {"used": mem[0], "total": mem[1]}
    return out


# --- quiet by default -------------------------------------------------------

def test_a_healthy_estate_says_nothing():
    """The most important test here. Silence is the default."""
    fleet = [
        asset(id=1, name="nas", has_agent=True, agent_last_seen=NOW,
              agent_metrics=metrics(disk=(500, 1000), mem=(8, 32))),
        asset(id=2, name="switch", warranty_expires=date(2027, 12, 1)),
        asset(id=3, name="laptop", eol_date=date(2029, 1, 1)),
    ]
    assert al.evaluate(fleet, NOW) == []
    assert al.summarise([]) == "All clear"


@pytest.mark.parametrize("pct", [0, 50, 80, 89])
def test_normal_disk_usage_is_not_an_alert(pct):
    a = asset(has_agent=True, agent_last_seen=NOW, agent_metrics=metrics(disk=(pct, 100)))
    assert al.check_disk(a, NOW) is None


def test_an_asset_with_no_agent_never_reports_offline():
    """Most assets are a switch or a cable, not a server. They cannot be 'down'."""
    assert al.check_offline(asset(has_agent=False), NOW) is None


def test_an_agent_that_never_connected_is_setup_not_an_incident():
    a = asset(has_agent=True, agent_last_seen=None)
    assert al.check_offline(a, NOW) is None


def test_a_brief_blip_is_not_an_outage():
    """90s is a reboot. Paging on it would get the channel muted."""
    a = asset(has_agent=True, agent_last_seen=NOW - timedelta(minutes=2))
    assert al.check_offline(a, NOW) is None


def test_archived_and_muted_assets_are_silent():
    broken = dict(has_agent=True, agent_last_seen=NOW - timedelta(days=3),
                  agent_metrics=metrics(disk=(99, 100)), warranty_expires=date(2020, 1, 1))
    assert al.evaluate([asset(archived=True, **broken)], NOW) == []
    assert al.evaluate([asset(muted=True, **broken)], NOW) == []


# --- it does fire when it should -------------------------------------------

def test_a_genuinely_dead_server_is_critical():
    a = asset(has_agent=True, agent_last_seen=NOW - timedelta(hours=2))
    hit = al.check_offline(a, NOW)
    assert hit and hit.severity == "critical"
    assert "stopped reporting" in hit.title
    assert "2.0 hours" in hit.detail


@pytest.mark.parametrize("pct,severity", [(90, "warning"), (95, "warning"), (96, "critical"), (100, "critical")])
def test_disk_thresholds(pct, severity):
    a = asset(has_agent=True, agent_last_seen=NOW, agent_metrics=metrics(disk=(pct, 100)))
    hit = al.check_disk(a, NOW)
    assert hit and hit.severity == severity


def test_offline_suppresses_stale_metric_alerts():
    """A dead box's last disk reading is stale. Reporting both is noise on top
    of a real problem -- the person needs one clear thing to act on."""
    a = asset(has_agent=True, agent_last_seen=NOW - timedelta(hours=6),
              agent_metrics=metrics(disk=(99, 100), mem=(99, 100)))
    rules = {x.rule for x in al.evaluate([a], NOW)}
    assert rules == {"offline"}


def test_lifecycle_alerts_survive_an_outage():
    """A warranty still expires while the box is down -- that is not stale data."""
    a = asset(has_agent=True, agent_last_seen=NOW - timedelta(hours=6),
              warranty_expires=TODAY + timedelta(days=10))
    rules = {x.rule for x in al.evaluate([a], NOW)}
    assert rules == {"offline", "warranty"}


# --- expiry bucketing -------------------------------------------------------

def test_warranty_fires_once_per_horizon_not_once_per_day():
    """The bug that would make this feature hated: 60 daily 'expires soon' pings."""
    keys = set()
    for days in range(0, 61):
        a = asset(warranty_expires=TODAY + timedelta(days=days))
        hit = al.check_warranty(a, NOW)
        if hit:
            keys.add(hit.key)
    # 60-day horizon and 14-day horizon. Two alerts across two months.
    assert keys == {"warranty:1:60", "warranty:1:14"}


def test_distant_warranty_is_silent():
    assert al.check_warranty(asset(warranty_expires=TODAY + timedelta(days=200)), NOW) is None


def test_expired_warranty_is_reported_once_with_a_stable_key():
    a = asset(warranty_expires=TODAY - timedelta(days=400))
    hit = al.check_warranty(a, NOW)
    assert hit and hit.key == "warranty:1:expired"
    assert "400 days ago" in hit.detail


def test_eol_uses_longer_horizons_than_warranty():
    """Replacing hardware needs budget lead time; renewing cover does not."""
    a = asset(eol_date=TODAY + timedelta(days=170))
    assert al.check_eol(a, NOW) is not None
    assert al.check_warranty(asset(warranty_expires=TODAY + timedelta(days=170)), NOW) is None


def test_no_dates_means_no_lifecycle_alerts():
    a = asset(warranty_expires=None, eol_date=None)
    assert al.check_warranty(a, NOW) is None and al.check_eol(a, NOW) is None


# --- cooldown ---------------------------------------------------------------

def test_first_sighting_always_sends():
    a = Alert("k", "disk", "critical", 1, "nas", "t", "d")
    assert al.should_send(a, None, NOW) is True


def test_critical_repeats_sooner_than_warning():
    crit = Alert("k", "disk", "critical", 1, "nas", "t", "d")
    warn = Alert("k", "warranty", "warning", 1, "nas", "t", "d")
    eleven_h = NOW - timedelta(hours=11)
    assert al.should_send(crit, eleven_h, NOW) is False
    assert al.should_send(crit, NOW - timedelta(hours=13), NOW) is True
    assert al.should_send(warn, NOW - timedelta(days=2), NOW) is False
    assert al.should_send(warn, NOW - timedelta(days=4), NOW) is True


# --- robustness: bad data must not crash the whole run ----------------------

@pytest.mark.parametrize("m", [
    None, {}, {"disk": None}, {"disk": {}}, {"disk": {"used": 5}},
    {"disk": {"used": None, "total": None}}, {"disk": {"used": "x", "total": "y"}},
    {"disk": {"used": 5, "total": 0}},
])
def test_malformed_metrics_are_ignored_not_fatal(m):
    """Agents are third-party scripts on someone else's box. Assume garbage."""
    a = asset(has_agent=True, agent_last_seen=NOW, agent_metrics=m)
    assert al.check_disk(a, NOW) is None
    assert al.evaluate([a], NOW) == []


def test_divide_by_zero_disk_is_not_an_alert():
    a = asset(has_agent=True, agent_last_seen=NOW, agent_metrics=metrics(disk=(0, 0)))
    assert al.check_disk(a, NOW) is None


def test_unknown_severity_is_rejected_at_construction():
    with pytest.raises(ValueError):
        Alert("k", "disk", "catastrophic", 1, "nas", "t", "d")


# --- ordering and summary ---------------------------------------------------

def test_critical_sorts_above_warning():
    fleet = [
        asset(id=1, name="zzz-dead", has_agent=True, agent_last_seen=NOW - timedelta(hours=9)),
        asset(id=2, name="aaa-warranty", warranty_expires=TODAY + timedelta(days=5)),
    ]
    got = al.evaluate(fleet, NOW)
    assert [x.severity for x in got] == ["critical", "warning"]


def test_alert_keys_are_unique_per_problem():
    """Dedupe depends on this: two different problems must not share a key."""
    a = asset(has_agent=True, agent_last_seen=NOW,
              agent_metrics=metrics(disk=(99, 100), mem=(99, 100)),
              warranty_expires=TODAY + timedelta(days=5),
              eol_date=TODAY + timedelta(days=20))
    got = al.evaluate([a], NOW)
    assert len(got) == len({x.key for x in got}) == 4


def test_summary_reads_like_a_human_wrote_it():
    crit = Alert("a", "disk", "critical", 1, "n", "t", "d")
    warn = Alert("b", "warranty", "warning", 1, "n", "t", "d")
    assert al.summarise([crit, warn]) == "1 critical, 1 warning"
    assert al.summarise([warn, warn]) == "2 warnings"
    assert al.summarise([crit]) == "1 critical"
