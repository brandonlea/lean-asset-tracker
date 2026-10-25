from datetime import datetime, timedelta, timezone

from app import agent as ag
from app.auth import LoginThrottle, hash_password, password_problem, verify_password


def test_password_hash_roundtrip_and_salt():
    h1, h2 = hash_password("correct horse battery"), hash_password("correct horse battery")
    assert h1 != h2                                   # unique salt per hash
    assert verify_password("correct horse battery", h1)
    assert not verify_password("wrong", h1)
    assert not verify_password("x", "garbage")       # malformed stored value never crashes


def test_password_policy():
    assert password_problem("short")
    assert password_problem("password123")
    assert password_problem("a-decent-passphrase") is None


def test_throttle_blocks_after_limit():
    t = LoginThrottle(limit=3, window=60)
    for _ in range(3):
        assert not t.blocked("1.2.3.4")
        t.fail("1.2.3.4")
    assert t.blocked("1.2.3.4")
    assert not t.blocked("5.6.7.8")                   # per-IP
    t.reset("1.2.3.4")
    assert not t.blocked("1.2.3.4")


def test_agent_metrics_whitelist_drops_junk():
    dirty = {"hostname": "nas", "cpu_percent": 42.5, "memory": {"total": 8e9, "used": 2e9},
             "evil": "<script>", "cpu_count": "lots", "load": [0.1, "x", 0.3, 9, 9]}
    m = ag.clean_metrics(dirty)
    assert "evil" not in m
    assert m["cpu_count"] is None                     # wrong type rejected
    assert m["load"] == [0.1, 0.3]                    # non-numbers dropped, max 3 kept
    assert m["cpu_percent"] == 42.5
    assert ag.clean_metrics({"cpu_percent": 500})["cpu_percent"] is None  # out of range


def test_agent_status_thresholds():
    now = datetime.now(timezone.utc)
    assert ag.status(None) == "never"
    assert ag.status(now - timedelta(seconds=30), now) == "online"
    assert ag.status(now - timedelta(seconds=91), now) == "offline"


def test_token_format_and_hash():
    t = ag.new_token()
    assert t.startswith("lat_") and len(t) > 30
    assert ag.token_hash(t) != t and len(ag.token_hash(t)) == 64


def test_humanisers():
    assert ag.human_bytes(1536) == "2 KB"
    assert ag.human_bytes(8 * 1024**3) == "8.0 GB"
    assert ag.human_uptime(90061) == "1d 1h"
    assert ag.pct(1, 4) == 25 and ag.pct(1, 0) is None


def test_location_parent_chain_is_many_to_one():
    """Regression: the relationship was inverted, making .parent a list and crashing location_path."""
    from app.main import location_path
    from app.models import Location
    loft = Location(id=1, name="Loft")
    rack = Location(id=2, name="Rack A", parent=loft)
    u12 = Location(id=3, name="U12", parent=rack)
    assert rack.parent is loft and not isinstance(rack.parent, list)
    assert location_path(u12) == "Loft / Rack A / U12"
    assert location_path(None) == ""
