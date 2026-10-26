"""Turning alerts into webhook payloads.

Self-hosted installs have no mail server (same reason invites are links, not
emails), so delivery is webhooks: Discord, Slack, ntfy and generic JSON cover
essentially every homelab. Formatting is pure and tested; the actual POST is
the caller's job.

Detection is by URL, not a dropdown the user has to get right. Pasting a
Discord webhook and picking "Slack" from a menu is an obvious trap, and the
URL already tells us everything.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from .alerts import Alert

COLOURS = {"critical": 0xDC2626, "warning": 0xF59E0B}   # red, amber
EMOJI = {"critical": "🔴", "warning": "🟡"}
NTFY_PRIORITY = {"critical": "urgent", "warning": "default"}
MAX_BATCH = 10          # more than this in one message is a wall of text


class ChannelError(ValueError):
    pass


def detect_kind(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    path = (urlparse(url).path or "").lower()
    if host.endswith("discord.com") or host.endswith("discordapp.com"):
        return "discord"
    if host.endswith("slack.com"):
        return "slack"
    if host == "ntfy.sh" or path.startswith("/ntfy") or host.startswith("ntfy."):
        return "ntfy"
    return "generic"


def validate_url(url: str) -> str:
    """Reject anything that isn't a plain outbound http(s) webhook.

    Blocking non-http schemes matters: this URL is fetched by the server, so
    `file://` would be a read primitive. We deliberately do NOT block private
    IPs -- a homelab's whole point is that ntfy runs on 192.168.1.x, and an
    SSRF guard here would break the main use case while barely slowing an
    attacker who already has admin on the instance.
    """
    url = (url or "").strip()
    if not url:
        raise ChannelError("Enter a webhook URL.")
    if len(url) > 2000:
        raise ChannelError("That URL is too long.")
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ChannelError("The URL must start with http:// or https://")
    if not p.hostname:
        raise ChannelError("That doesn't look like a URL.")
    return url


def _line(a: Alert) -> str:
    return f"{EMOJI[a.severity]} **{a.title}**\n{a.detail}"


def _trim(alerts: list[Alert]) -> tuple[list[Alert], int]:
    return alerts[:MAX_BATCH], max(0, len(alerts) - MAX_BATCH)


def build_payload(alerts: list[Alert], url: str, site_name: str = "Lean Asset Tracker",
                  link: str | None = None) -> tuple[dict | str, dict]:
    """-> (body, headers). Body is a dict for JSON channels, str for ntfy."""
    if not alerts:
        raise ChannelError("Nothing to send.")
    kind = detect_kind(url)
    shown, extra = _trim(alerts)
    worst = "critical" if any(a.severity == "critical" for a in alerts) else "warning"
    more = f"\n…and {extra} more." if extra else ""

    if kind == "discord":
        return {
            "username": site_name,
            "embeds": [{
                "title": f"{EMOJI[worst]} {len(alerts)} alert{'s' if len(alerts) > 1 else ''}",
                "description": "\n\n".join(_line(a) for a in shown) + more,
                "color": COLOURS[worst],
                **({"url": link} if link else {}),
            }],
        }, {"Content-Type": "application/json"}

    if kind == "slack":
        return {
            "text": f"{EMOJI[worst]} {site_name}: {len(alerts)} alert{'s' if len(alerts) > 1 else ''}",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": _line(a)}}
                       for a in shown],
        }, {"Content-Type": "application/json"}

    if kind == "ntfy":
        body = "\n\n".join(f"{a.title}\n{a.detail}" for a in shown) + more
        return body, {
            "Title": f"{site_name}: {len(alerts)} alert{'s' if len(alerts) > 1 else ''}",
            "Priority": NTFY_PRIORITY[worst],
            "Tags": "warning" if worst == "critical" else "bell",
        }

    # generic: stable, documented JSON others can parse
    return {
        "source": site_name,
        "count": len(alerts),
        "severity": worst,
        **({"link": link} if link else {}),
        "alerts": [{"key": a.key, "rule": a.rule, "severity": a.severity,
                    "asset_id": a.asset_id, "asset": a.asset_name,
                    "title": a.title, "detail": a.detail} for a in alerts],
    }, {"Content-Type": "application/json"}


def encode(body: dict | str) -> bytes:
    return body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
