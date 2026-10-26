"""Alert rules. Pure functions: no DB, no HTTP, no clock of their own.

Everything takes an explicit `now` and plain data, so the awkward cases
(a warranty that expired last year, a server that has never checked in, a
disk that is full but the box is offline) are unit-testable without standing
up Postgres.

Design notes that are easy to get wrong and expensive to get wrong:

* **Alerting is a trust system.** A monitor that cries wolf gets muted, and a
  muted monitor is worse than none because it looks like cover. So every rule
  here fires on a *state change*, and `should_send` enforces a cooldown. The
  default posture is silence.

* **Offline beats disk.** If a machine stopped reporting, its last known disk
  figure is stale and alerting on it is noise on top of a real problem. The
  offline rule suppresses the metric rules for that asset.

* **Severity is about what the person should do**, not how bad the number
  looks. `critical` = act now, `warning` = plan it in. Nothing else exists,
  because a five-level scheme just moves the argument.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# --- thresholds -------------------------------------------------------------
# Deliberately generous. A homelab disk at 80% is normal and not news.
DISK_WARNING_PCT = 90
DISK_CRITICAL_PCT = 96
MEMORY_WARNING_PCT = 95

# A box that has missed heartbeats for 15 minutes is a real event; 90 seconds
# (the UI's "offline" dot) is just a reboot or a flaky wifi link, and paging
# on it would make the feature unusable.
OFFLINE_AFTER = timedelta(minutes=15)

# Expiry horizons, in days. Long enough to actually do something about it:
# renewing a warranty or budgeting a replacement is not a same-week job.
WARRANTY_HORIZONS = (60, 14)
EOL_HORIZONS = (180, 30)

# How long before the same alert may repeat.
COOLDOWN = {"critical": timedelta(hours=12), "warning": timedelta(days=3)}

SEVERITIES = ("critical", "warning")


@dataclass(frozen=True)
class Alert:
    key: str          # stable identity, for dedupe: "<rule>:<asset_id>:<bucket>"
    rule: str
    severity: str
    asset_id: int | None
    asset_name: str
    title: str
    detail: str

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")


@dataclass
class AssetView:
    """The slice of an asset the rules need. Keeps them DB-free."""
    id: int
    name: str
    warranty_expires: date | None = None
    eol_date: date | None = None
    agent_last_seen: datetime | None = None
    agent_metrics: dict | None = None
    has_agent: bool = False
    status: str = "active"
    archived: bool = False
    muted: bool = False


def _pct(part, whole) -> float | None:
    try:
        part, whole = float(part), float(whole)
    except (TypeError, ValueError):
        return None
    return None if whole <= 0 else round(part / whole * 100, 1)


def _days(target: date, today: date) -> int:
    return (target - today).days


def _horizon(days_left: int, horizons: tuple[int, ...]) -> int | None:
    """Smallest horizon the value has crossed, or None.

    Bucketing by horizon (not by day) is what stops a 60-day warranty alert
    repeating 60 times. Crossing 60 fires once; crossing 14 fires once more.
    """
    crossed = [h for h in sorted(horizons) if days_left <= h]
    return crossed[0] if crossed else None


# --- individual rules -------------------------------------------------------

def check_offline(a: AssetView, now: datetime) -> Alert | None:
    if not a.has_agent:
        return None
    if a.agent_last_seen is None:
        return None          # never connected: that's setup, not an incident
    late = now - a.agent_last_seen
    if late < OFFLINE_AFTER:
        return None
    hours = late.total_seconds() / 3600
    pretty = f"{late.total_seconds() / 60:.0f} minutes" if hours < 1 else f"{hours:.1f} hours"
    return Alert(
        key=f"offline:{a.id}", rule="offline", severity="critical", asset_id=a.id,
        asset_name=a.name, title=f"{a.name} has stopped reporting",
        detail=f"No heartbeat for {pretty}. Last seen {a.agent_last_seen:%Y-%m-%d %H:%M UTC}.",
    )


def check_disk(a: AssetView, now: datetime) -> Alert | None:
    disk = (a.agent_metrics or {}).get("disk") or {}
    pct = _pct(disk.get("used"), disk.get("total"))
    if pct is None or pct < DISK_WARNING_PCT:
        return None
    critical = pct >= DISK_CRITICAL_PCT
    # bucket by threshold so drifting 90.1 -> 90.4 is not a new alert
    bucket = "critical" if critical else "warning"
    return Alert(
        key=f"disk:{a.id}:{bucket}", rule="disk",
        severity="critical" if critical else "warning", asset_id=a.id,
        asset_name=a.name, title=f"{a.name} disk {pct:.0f}% full",
        detail=f"Disk is at {pct:.0f}%. "
               + ("Act now — a full disk takes services down." if critical
                  else "Worth clearing space before it bites."),
    )


def check_memory(a: AssetView, now: datetime) -> Alert | None:
    mem = (a.agent_metrics or {}).get("memory") or {}
    pct = _pct(mem.get("used"), mem.get("total"))
    if pct is None or pct < MEMORY_WARNING_PCT:
        return None
    return Alert(
        key=f"memory:{a.id}", rule="memory", severity="warning", asset_id=a.id,
        asset_name=a.name, title=f"{a.name} memory {pct:.0f}% used",
        detail=f"Memory is at {pct:.0f}%. Check for a leak or a runaway process.",
    )


def check_warranty(a: AssetView, now: datetime) -> Alert | None:
    if not a.warranty_expires:
        return None
    left = _days(a.warranty_expires, now.date())
    if left < 0:
        return Alert(
            key=f"warranty:{a.id}:expired", rule="warranty", severity="warning",
            asset_id=a.id, asset_name=a.name,
            title=f"{a.name} is out of warranty",
            detail=f"Warranty ended {a.warranty_expires:%d %b %Y} ({abs(left)} days ago).",
        )
    h = _horizon(left, WARRANTY_HORIZONS)
    if h is None:
        return None
    return Alert(
        key=f"warranty:{a.id}:{h}", rule="warranty", severity="warning",
        asset_id=a.id, asset_name=a.name,
        title=f"{a.name} warranty expires in {left} days",
        detail=f"Cover ends {a.warranty_expires:%d %b %Y}. Renew or plan a replacement.",
    )


def check_eol(a: AssetView, now: datetime) -> Alert | None:
    if not a.eol_date:
        return None
    left = _days(a.eol_date, now.date())
    if left < 0:
        return Alert(
            key=f"eol:{a.id}:passed", rule="eol", severity="warning",
            asset_id=a.id, asset_name=a.name,
            title=f"{a.name} is past end of life",
            detail=f"End of life was {a.eol_date:%d %b %Y}. It may no longer get security updates.",
        )
    h = _horizon(left, EOL_HORIZONS)
    if h is None:
        return None
    return Alert(
        key=f"eol:{a.id}:{h}", rule="eol", severity="warning", asset_id=a.id,
        asset_name=a.name, title=f"{a.name} reaches end of life in {left} days",
        detail=f"End of life {a.eol_date:%d %b %Y}. Budget a replacement.",
    )


# Order matters only for readability; suppression is explicit below.
METRIC_RULES = (check_disk, check_memory)
LIFECYCLE_RULES = (check_warranty, check_eol)


def evaluate(assets, now: datetime | None = None) -> list[Alert]:
    """All alerts currently true for these assets, newest problems first."""
    now = now or datetime.now(timezone.utc)
    out: list[Alert] = []
    for a in assets:
        if a.archived or a.muted:
            continue
        offline = check_offline(a, now)
        if offline:
            out.append(offline)
        else:
            # Stale metrics from a dead box are noise, not signal.
            for rule in METRIC_RULES:
                if (hit := rule(a, now)):
                    out.append(hit)
        for rule in LIFECYCLE_RULES:
            if (hit := rule(a, now)):
                out.append(hit)
    out.sort(key=lambda x: (x.severity != "critical", x.asset_name.lower(), x.rule))
    return out


def should_send(alert: Alert, last_sent: datetime | None, now: datetime | None = None) -> bool:
    """Has this exact alert been quiet long enough to say again?"""
    if last_sent is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - last_sent) >= COOLDOWN[alert.severity]


def summarise(alerts: list[Alert]) -> str:
    if not alerts:
        return "All clear"
    crit = sum(1 for a in alerts if a.severity == "critical")
    warn = len(alerts) - crit
    bits = ([f"{crit} critical"] if crit else []) + ([f"{warn} warning{'s' if warn > 1 else ''}"] if warn else [])
    return ", ".join(bits)
