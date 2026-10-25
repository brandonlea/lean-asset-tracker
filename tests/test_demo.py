import pytest

from app import demo


def test_blocked_posts():
    for p in ["/assets/3/agent/token", "/assets/3/agent/revoke", "/api/agent/heartbeat", "/logout", "/setup", "/login", "/settings"]:
        assert demo.blocked_post(p), p
    for p in ["/assets/new", "/assets/3/edit", "/assets/3/archive", "/assets/3/maintenance", "/import/csv"]:
        assert not demo.blocked_post(p), p


def test_write_rate_limit():
    demo._writes.clear()
    assert not any(demo.write_limited("1.2.3.4", now=100.0 + i) for i in range(demo.WRITE_LIMIT))
    assert demo.write_limited("1.2.3.4", now=200.0)
    assert not demo.write_limited("5.6.7.8", now=200.0)                    # per IP
    assert not demo.write_limited("1.2.3.4", now=100.0 + demo.WRITE_WINDOW_S + 70)  # window slides


def test_reset_refuses_outside_demo(monkeypatch):
    monkeypatch.setattr(demo, "ENABLED", False)
    with pytest.raises(RuntimeError, match="LAT_DEMO is not 1"):
        demo.reset(None)


def test_sample_data_is_consistent():
    serials = [d[2] for d in demo.DEVICES]
    assert len(serials) == len(set(serials))
    sims = [d for d in demo.DEVICES if d[11]]
    assert sum(1 for d in sims if d[11].get("offline")) == 1           # one deliberately offline server
    for d in sims:
        m = demo._metrics(d[11])
        assert 0 <= m["cpu_percent"] <= 100 and m["memory"]["used"] <= m["memory"]["total"]
        assert m["disk"]["used"] <= m["disk"]["total"]
    # fake hashes can never collide with a real SHA-256 hex token hash
    assert not all(c in "0123456789abcdef" for c in demo.SIM_HASH_PREFIX)
