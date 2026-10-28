"""Turning alert rules into delivered messages.

app/alerts.py decides what is true. app/alert_sink.py decides what the message
looks like. This module is the only part that touches the database and the
network, so the other two stay trivially testable.

Practicality rules this module exists to enforce:

* A channel you cannot test is worthless, so send_test() posts a real message
  through the real code path -- not a special "test mode" that can pass while
  live delivery is broken.
* One dead channel must never stop the others, so every send is isolated.
* Nothing is recorded as sent unless at least one channel accepted it,
  otherwise a webhook outage would permanently silence an alert.
"""
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import alert_sink as sink
from . import alerts as rules

log = logging.getLogger("alert_dispatch")

TIMEOUT_S = 10
USER_AGENT = "LeanAssetTracker/alerts"


def build_views(db: Session) -> list[rules.AssetView]:
    """Project Asset rows into the DB-free shape the rules expect."""
    from .models import Asset
    out = []
    for a in db.query(Asset).all():
        out.append(rules.AssetView(
            id=a.id,
            name=a.name,
            warranty_expires=a.warranty_expires,
            eol_date=a.eol_date,
            agent_last_seen=a.agent_last_seen,
            agent_metrics=a.agent_metrics,
            has_agent=bool(a.agent_token_hash),
            status=a.status or "active",
            archived=a.archived_at is not None,
            muted=bool(a.alerts_muted),
        ))
    return out


def post(url: str, kind: str, alerts: list[rules.Alert], site_name: str) -> None:
    """POST one batch. Raises on any failure so the caller can record it.

    build_payload returns (body, headers) and BOTH matter: ntfy takes its
    title, priority and tags from the headers, so dropping them produces
    untitled notifications at the wrong priority. Treating the whole tuple as
    the body also serialises the header dict into the JSON, which a generic
    receiver cannot parse. Both bugs shipped once and were only caught by
    posting to a real listener -- see test_post_sends_only_the_body.
    """
    body, extra_headers = sink.build_payload(alerts, url, site_name=site_name)
    data = sink.encode(body)
    headers = {"User-Agent": USER_AGENT, **extra_headers}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        if r.status >= 300:
            raise urllib.error.HTTPError(url, r.status, "rejected", r.headers, None)


def _describe(e: Exception) -> str:
    """A message the user can act on, short enough for a table cell."""
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} from the webhook"
    if isinstance(e, urllib.error.URLError):
        return f"Could not reach the host ({getattr(e, 'reason', 'unknown')})"
    if isinstance(e, sink.ChannelError):
        return str(e)
    return type(e).__name__


def deliver(db: Session, alerts: list[rules.Alert], site_name: str = "Lean Asset Tracker") -> dict:
    """Send one batch to every enabled channel. Returns a per-channel result.

    Each channel is isolated: a 404 on one webhook must not stop the others,
    because the whole point of having two channels is redundancy.
    """
    from .models import AlertChannel
    results: dict[int, str | None] = {}
    now = datetime.now(timezone.utc)
    channels = db.query(AlertChannel).filter(AlertChannel.enabled.is_(True)).all()
    for ch in channels:
        try:
            post(ch.url, ch.kind, alerts, site_name)
            ch.last_ok_at, ch.last_error = now, None
            results[ch.id] = None
        except Exception as e:                      # noqa: BLE001 - recorded, not raised
            ch.last_error = _describe(e)
            results[ch.id] = ch.last_error
            log.warning("alert channel %s failed: %s", ch.id, ch.last_error)
    db.commit()
    return results


def run_once(db: Session, now: datetime | None = None,
             site_name: str = "Lean Asset Tracker") -> dict:
    """Evaluate every asset and send whatever is both true and not recently said.

    Returns a summary for logging and for the settings page, so the user can
    see the dispatcher is alive even when it is correctly silent.
    """
    from .models import AlertChannel, AlertSent
    now = now or datetime.now(timezone.utc)
    found = rules.evaluate(build_views(db), now)

    if not db.query(AlertChannel).filter(AlertChannel.enabled.is_(True)).count():
        # Nowhere to send. Do NOT record these as sent, or the first channel a
        # user adds would start out silent about problems that already exist.
        return {"found": len(found), "sent": 0, "channels": 0, "skipped_no_channel": True}

    sent_rows = {r.key: r for r in db.query(AlertSent).all()}
    due = [a for a in found
           if rules.should_send(a, getattr(sent_rows.get(a.key), "last_sent_at", None), now)]
    if not due:
        return {"found": len(found), "sent": 0, "channels": 0, "quiet": True}

    results = deliver(db, due, site_name)
    accepted = [cid for cid, err in results.items() if err is None]
    if accepted:
        for a in due:
            row = sent_rows.get(a.key)
            if row is None:
                db.add(AlertSent(key=a.key, severity=a.severity, asset_id=a.asset_id,
                                 title=a.title, last_sent_at=now))
            else:
                row.severity, row.title, row.last_sent_at = a.severity, a.title, now
        db.commit()
    return {"found": len(found), "sent": len(due) if accepted else 0,
            "channels": len(accepted), "errors": [e for e in results.values() if e]}


def send_test(db: Session, channel_id: int, site_name: str = "Lean Asset Tracker") -> str | None:
    """Post a real message through the real path. None means success.

    Deliberately not a mocked "test mode": the only test worth having is the
    one that fails when live delivery would fail.
    """
    from .models import AlertChannel
    ch = db.get(AlertChannel, channel_id)
    if ch is None:
        return "That channel no longer exists."
    demo = rules.Alert(
        key="test", rule="test", severity="warning", asset_id=None,
        asset_name="Lean Asset Tracker",
        title="Test alert from Lean Asset Tracker",
        detail="If you can read this, alerts will reach you. Nothing is wrong.",
    )
    now = datetime.now(timezone.utc)
    try:
        post(ch.url, ch.kind, [demo], site_name)
    except Exception as e:                          # noqa: BLE001
        ch.last_error = _describe(e)
        db.commit()
        return ch.last_error
    ch.last_ok_at, ch.last_error = now, None
    db.commit()
    return None
