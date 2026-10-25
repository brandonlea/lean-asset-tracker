"""Server-agent helpers: token handling, payload validation, status/metric formatting."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from .auth import sha256

ONLINE_AFTER_S = 90          # 3 missed 30 s heartbeats = offline
MAX_PAYLOAD_BYTES = 16_384


def new_token() -> str:
    return "lat_" + secrets.token_urlsafe(24)


def token_hash(token: str) -> str:
    return sha256(token)


def _num(v, lo=0, hi=1e18):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi else None


def _s(v, n=120):
    return str(v)[:n] if isinstance(v, (str, int, float)) else None


def clean_metrics(p: dict) -> dict:
    """Whitelist + type-check agent data. Anything unexpected is dropped, never stored raw."""
    mem, disk = p.get("memory") or {}, p.get("disk") or {}
    load = p.get("load") if isinstance(p.get("load"), list) else []
    return {
        "agent_version": _s(p.get("agent_version"), 20),
        "hostname": _s(p.get("hostname"), 100),
        "os": _s(p.get("os")),
        "kernel": _s(p.get("kernel")),
        "cpu_count": _num(p.get("cpu_count"), 1, 4096),
        "cpu_percent": _num(p.get("cpu_percent"), 0, 100),
        "load": [x for x in (_num(v, 0, 1e6) for v in load[:3]) if x is not None],
        "memory": {"total": _num(mem.get("total") if isinstance(mem, dict) else None),
                   "used": _num(mem.get("used") if isinstance(mem, dict) else None)},
        "disk": {"total": _num(disk.get("total") if isinstance(disk, dict) else None),
                 "used": _num(disk.get("used") if isinstance(disk, dict) else None)},
        "uptime_s": _num(p.get("uptime_s")),
    }


def status(last_seen: datetime | None, now: datetime | None = None) -> str:
    if not last_seen:
        return "never"
    now = now or datetime.now(timezone.utc)
    return "online" if (now - last_seen).total_seconds() <= ONLINE_AFTER_S else "offline"


def ago(last_seen: datetime | None, now: datetime | None = None) -> str:
    if not last_seen:
        return "never"
    s = int(((now or datetime.now(timezone.utc)) - last_seen).total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def human_bytes(n) -> str:
    if not isinstance(n, (int, float)):
        return "–"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def human_uptime(s) -> str:
    if not isinstance(s, (int, float)):
        return "–"
    d, rem = divmod(int(s), 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}d {h}h" if d else f"{h}h {rem // 60}m"


def pct(used, total) -> int | None:
    return round(100 * used / total) if isinstance(used, (int, float)) and total else None
