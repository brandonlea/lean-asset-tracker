"""Alert delivery and the settings UI.

The pure rules are covered in test_alerts.py and the payload shapes in
test_alert_sink.py. These tests are about the things that make alerting
*practical*: that a channel can be verified, that one broken webhook cannot
silence another, and that a delivery failure never marks an alert as "said".
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import alert_dispatch as dispatch
from app import alerts as rules
from app.models import AlertChannel, AlertSent, Asset


def _alert(key="disk:1:warning", severity="warning"):
    return rules.Alert(key=key, rule="disk", severity=severity, asset_id=1,
                       asset_name="nas", title="nas disk 95% full", detail="Clear space.")


# ----------------------------------------------------------------- the test button
def test_send_test_posts_through_the_real_path(db_session):
    """A 'test mode' that bypasses delivery could pass while real sends fail."""
    ch = AlertChannel(name="Phone", url="https://discord.com/api/webhooks/1/abc",
                      kind="discord", enabled=True)
    db_session.add(ch)
    db_session.commit()
    with patch.object(dispatch, "post") as p:
        assert dispatch.send_test(db_session, ch.id) is None
        assert p.called, "send_test must go through dispatch.post"
    db_session.refresh(ch)
    assert ch.last_ok_at is not None and ch.last_error is None


def test_send_test_records_a_readable_error(db_session):
    ch = AlertChannel(name="Broken", url="https://example.invalid/hook",
                      kind="generic", enabled=True)
    db_session.add(ch)
    db_session.commit()
    import urllib.error
    with patch.object(dispatch, "post", side_effect=urllib.error.HTTPError(
            "u", 404, "nf", None, None)):
        err = dispatch.send_test(db_session, ch.id)
    assert err and "404" in err
    db_session.refresh(ch)
    assert ch.last_error and "404" in ch.last_error


def test_send_test_on_a_missing_channel_is_graceful(db_session):
    assert "no longer exists" in (dispatch.send_test(db_session, 99999) or "")


# -------------------------------------------------------------- channel isolation
def test_one_dead_channel_does_not_stop_the_others(db_session):
    """Redundancy is the entire reason to configure two channels."""
    good = AlertChannel(name="Good", url="https://ntfy.sh/mytopic", kind="ntfy", enabled=True)
    bad = AlertChannel(name="Bad", url="https://example.invalid/x", kind="generic", enabled=True)
    db_session.add_all([good, bad])
    db_session.commit()

    def fake_post(url, kind, alerts, site_name):
        if "invalid" in url:
            raise OSError("boom")

    with patch.object(dispatch, "post", side_effect=fake_post):
        res = dispatch.deliver(db_session, [_alert()])
    assert res[good.id] is None
    assert res[bad.id] is not None


def test_disabled_channels_are_skipped(db_session):
    ch = AlertChannel(name="Paused", url="https://ntfy.sh/t", kind="ntfy", enabled=False)
    db_session.add(ch)
    db_session.commit()
    with patch.object(dispatch, "post") as p:
        dispatch.deliver(db_session, [_alert()])
    assert not p.called


# ------------------------------------------------------- never lie about "sent"
def test_nothing_is_marked_sent_when_every_channel_fails(db_session):
    """Otherwise a webhook outage would permanently silence a real problem."""
    db_session.add(Asset(name="nas", category="server", status="active",
                         agent_token_hash="h", agent_last_seen=datetime.now(timezone.utc),
                         agent_metrics={"disk": {"total": 100, "used": 99}}))
    db_session.add(AlertChannel(name="Bad", url="https://example.invalid/x",
                                kind="generic", enabled=True))
    db_session.commit()
    with patch.object(dispatch, "post", side_effect=OSError("down")):
        r = dispatch.run_once(db_session)
    assert r["sent"] == 0
    assert db_session.query(AlertSent).count() == 0, "a failed send must not be recorded"


def test_with_no_channels_nothing_is_recorded(db_session):
    """The first channel a user adds must report problems that already exist."""
    db_session.add(Asset(name="nas", category="server", status="active",
                         agent_token_hash="h", agent_last_seen=datetime.now(timezone.utc),
                         agent_metrics={"disk": {"total": 100, "used": 99}}))
    db_session.commit()
    r = dispatch.run_once(db_session)
    assert r.get("skipped_no_channel") is True
    assert db_session.query(AlertSent).count() == 0


def test_a_successful_send_is_recorded_and_then_held_back(db_session):
    db_session.add(Asset(name="nas", category="server", status="active",
                         agent_token_hash="h", agent_last_seen=datetime.now(timezone.utc),
                         agent_metrics={"disk": {"total": 100, "used": 99}}))
    db_session.add(AlertChannel(name="Good", url="https://ntfy.sh/t", kind="ntfy", enabled=True))
    db_session.commit()
    with patch.object(dispatch, "post"):
        first = dispatch.run_once(db_session)
        assert first["sent"] >= 1
        second = dispatch.run_once(db_session)
    assert second["sent"] == 0, "the same alert must not repeat within the cooldown"


def test_muted_assets_produce_nothing(db_session):
    db_session.add(Asset(name="offbox", category="server", status="active",
                         agent_token_hash="h", alerts_muted=True,
                         agent_last_seen=datetime.now(timezone.utc) - timedelta(hours=5),
                         agent_metrics={"disk": {"total": 100, "used": 99}}))
    db_session.add(AlertChannel(name="Good", url="https://ntfy.sh/t", kind="ntfy", enabled=True))
    db_session.commit()
    with patch.object(dispatch, "post") as p:
        r = dispatch.run_once(db_session)
    assert r["found"] == 0 and not p.called


# --------------------------------------------------------------- the settings UI
def test_alerts_page_requires_admin(client_member):
    assert client_member.get("/settings/alerts").status_code == 403


def test_alerts_page_lists_thresholds_in_plain_words(client_admin):
    r = client_admin.get("/settings/alerts")
    assert r.status_code == 200
    body = r.text
    assert "Server offline" in body and "Disk filling" in body
    assert "15 minutes" in body, "the offline window must be stated, not implied"


def test_adding_a_channel_rejects_a_dangerous_url(client_admin):
    r = client_admin.post("/settings/alerts", data={"name": "Bad", "url": "file:///etc/passwd"})
    assert r.status_code == 200
    assert "http" in r.text.lower()        # an error was shown, channel not created


def test_adding_a_channel_requires_a_name(client_admin):
    r = client_admin.post("/settings/alerts",
                          data={"name": "  ", "url": "https://ntfy.sh/t"})
    assert "name" in r.text.lower()


def test_adding_a_channel_tests_it_immediately(client_admin):
    """The whole point: you never leave this page with an unverified channel."""
    with patch.object(dispatch, "post") as p:
        r = client_admin.post("/settings/alerts",
                              data={"name": "Phone", "url": "https://ntfy.sh/mytopic"})
    assert r.status_code == 200
    assert p.called, "saving a channel must send a test message"
    assert "test message" in r.text.lower()


def test_a_failing_new_channel_says_so_rather_than_claiming_success(client_admin):
    with patch.object(dispatch, "post", side_effect=OSError("nope")):
        r = client_admin.post("/settings/alerts",
                              data={"name": "Phone", "url": "https://ntfy.sh/mytopic"})
    assert "test failed" in r.text.lower()


def test_check_now_with_no_channels_tells_you_why_nothing_happened(client_admin):
    r = client_admin.post("/settings/alerts/run")
    assert "add a channel" in r.text.lower()


# ------------------------------------------------- what goes on the wire, exactly
# These mock urlopen, NOT dispatch.post, because the bug they guard lived inside
# post(): build_payload returns (body, headers) and post() was sending the whole
# tuple as the body. Every test above mocks post() and so could never see it.
# It was found by posting to a real listener from the running container.
def _captured_request(url):
    from unittest.mock import MagicMock
    import urllib.request
    seen = {}

    class R:
        status = 204
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.headers)
        seen["data"] = req.data
        return R()

    with patch.object(urllib.request, "urlopen", fake_urlopen):
        dispatch.post(url, dispatch.sink.detect_kind(url), [_alert()], "Lean Asset Tracker")
    return seen


def test_post_sends_only_the_body_never_the_headers_tuple():
    """Regression: the header dict must not end up inside the JSON payload."""
    import json
    seen = _captured_request("https://example.com/hook")
    payload = json.loads(seen["data"].decode())
    assert isinstance(payload, dict), f"body must be the payload object, got {type(payload)}"
    assert set(payload) >= {"source", "count", "severity", "alerts"}
    assert "Content-Type" not in payload, "headers leaked into the body"


def test_generic_post_sets_json_content_type():
    seen = _captured_request("https://example.com/hook")
    assert seen["headers"].get("Content-type") == "application/json"


def test_ntfy_headers_are_actually_sent():
    """ntfy takes its title and priority from headers; dropping them silently
    produces untitled notifications at the wrong priority."""
    seen = _captured_request("https://ntfy.sh/mytopic")
    h = {k.lower(): v for k, v in seen["headers"].items()}
    assert "title" in h and "priority" in h, f"ntfy headers missing: {h}"
    assert seen["data"].decode().startswith("nas disk"), "ntfy body should be plain text"


def test_discord_body_is_an_embed_object():
    import json
    seen = _captured_request("https://discord.com/api/webhooks/1/abc")
    payload = json.loads(seen["data"].decode())
    assert "embeds" in payload and isinstance(payload["embeds"], list)


def test_user_agent_is_always_set():
    seen = _captured_request("https://example.com/hook")
    assert "LeanAssetTracker" in seen["headers"].get("User-agent", "")
