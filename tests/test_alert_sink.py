"""Webhook payload building."""
import json

import pytest

from app import alert_sink as sink
from app.alerts import Alert

CRIT = Alert("disk:1:critical", "disk", "critical", 1, "nas-01", "nas-01 disk 97% full", "Act now.")
WARN = Alert("warranty:2:60", "warranty", "warning", 2, "sw-01", "sw-01 warranty expires in 60 days", "Renew it.")


@pytest.mark.parametrize("url,kind", [
    ("https://discord.com/api/webhooks/1/abc", "discord"),
    ("https://discordapp.com/api/webhooks/1/abc", "discord"),
    ("https://hooks.slack.com/services/T/B/x", "slack"),
    ("https://ntfy.sh/my-homelab", "ntfy"),
    ("https://ntfy.example.org/alerts", "ntfy"),
    ("http://192.168.1.50:8080/hook", "generic"),
    ("https://example.com/anything", "generic"),
])
def test_channel_detected_from_the_url(url, kind):
    """Asking the user to pick the type from a menu is a trap they will fail."""
    assert sink.detect_kind(url) == kind


@pytest.mark.parametrize("bad", ["", "   ", "file:///etc/passwd", "ftp://x/y", "javascript:alert(1)", "notaurl"])
def test_dangerous_or_empty_urls_are_rejected(bad):
    """The server fetches this URL, so file:// would be a read primitive."""
    with pytest.raises(sink.ChannelError):
        sink.validate_url(bad)


def test_over_long_url_rejected():
    with pytest.raises(sink.ChannelError):
        sink.validate_url("https://e.com/" + "a" * 2100)


def test_private_addresses_are_allowed_on_purpose():
    """A homelab runs ntfy on the LAN. Blocking RFC1918 would break the
    primary use case for the sake of a guard that stops nobody with admin."""
    for u in ["http://192.168.1.10/hook", "http://10.0.0.5:8080/x", "http://nas.local/y"]:
        assert sink.validate_url(u) == u


def test_discord_payload_shape():
    body, headers = sink.build_payload([CRIT, WARN], "https://discord.com/api/webhooks/1/a")
    assert headers["Content-Type"] == "application/json"
    embed = body["embeds"][0]
    assert embed["color"] == 0xDC2626          # worst severity drives the colour
    assert "2 alerts" in embed["title"]
    assert "nas-01 disk 97% full" in embed["description"]
    assert "sw-01" in embed["description"]
    json.dumps(body)                            # must be serialisable


def test_slack_payload_shape():
    body, _ = sink.build_payload([CRIT], "https://hooks.slack.com/services/T/B/x")
    assert "1 alert" in body["text"]
    assert body["blocks"][0]["text"]["type"] == "mrkdwn"


def test_ntfy_sends_plain_text_with_priority_headers():
    body, headers = sink.build_payload([CRIT], "https://ntfy.sh/lab")
    assert isinstance(body, str)
    assert headers["Priority"] == "urgent"
    assert "nas-01" in body


def test_ntfy_warning_is_not_urgent():
    _, headers = sink.build_payload([WARN], "https://ntfy.sh/lab")
    assert headers["Priority"] == "default"


def test_generic_payload_is_complete_and_documented():
    """Generic consumers get every alert, not the trimmed display list."""
    many = [Alert(f"k{i}", "disk", "warning", i, f"a{i}", "t", "d") for i in range(25)]
    body, _ = sink.build_payload(many, "https://example.com/hook")
    assert body["count"] == 25
    assert len(body["alerts"]) == 25
    assert set(body["alerts"][0]) == {"key", "rule", "severity", "asset_id", "asset", "title", "detail"}


def test_chat_channels_are_trimmed_so_they_do_not_wall_of_text():
    many = [Alert(f"k{i}", "disk", "warning", i, f"a{i}", f"title{i}", "d") for i in range(25)]
    body, _ = sink.build_payload(many, "https://discord.com/api/webhooks/1/a")
    desc = body["embeds"][0]["description"]
    assert "and 15 more" in desc
    assert "title10" not in desc
    assert "25 alerts" in body["embeds"][0]["title"]   # the true count still shows


def test_single_alert_is_not_pluralised():
    body, _ = sink.build_payload([CRIT], "https://discord.com/api/webhooks/1/a")
    assert "1 alert" in body["embeds"][0]["title"]
    assert "1 alerts" not in body["embeds"][0]["title"]


def test_sending_nothing_is_an_error_not_an_empty_ping():
    with pytest.raises(sink.ChannelError):
        sink.build_payload([], "https://discord.com/api/webhooks/1/a")


def test_link_is_included_when_given():
    body, _ = sink.build_payload([CRIT], "https://example.com/h", link="https://lat.example/alerts")
    assert body["link"] == "https://lat.example/alerts"


def test_encode_handles_both_body_types():
    assert sink.encode("hello") == b"hello"
    assert json.loads(sink.encode({"a": 1})) == {"a": 1}
