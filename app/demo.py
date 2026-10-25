# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public demo mode (LAT_DEMO=1).

* No login: every visitor is the demo user. Nothing private should ever be put in a demo instance.
* Realistic sample data, reset with `python -m app.demo reset` (run nightly from cron/systemd).
* A few servers get simulated live-agent heartbeats so the Online/Offline view can be tried.
* Abuse limits: per-IP write rate limit, device cap, small CSV imports, agent connect disabled.
"""
from __future__ import annotations

import os
import random
import secrets
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

ENABLED = os.environ.get("LAT_DEMO", "").strip() == "1"
DEMO_EMAIL = "demo@example.com"
MAX_DEVICES = 300
MAX_IMPORT_BYTES = 64 * 1024
WRITE_LIMIT, WRITE_WINDOW_S = 60, 600
SIM_HASH_PREFIX = "demo-sim-"          # fake agent token hashes; no real token can ever match these
RESET_NOTE = os.environ.get("LAT_DEMO_RESET_NOTE", "Changes reset every night at 03:00 UK time.")

# Paths the public must not reach in demo mode (POST only).
BLOCKED_POSTS = ("/agent/token", "/agent/revoke", "/api/agent/heartbeat", "/logout", "/setup", "/login", "/settings")

_writes: dict[str, deque] = defaultdict(deque)


def write_limited(ip: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    q = _writes[ip]
    while q and now - q[0] > WRITE_WINDOW_S:
        q.popleft()
    if len(q) >= WRITE_LIMIT:
        return True
    q.append(now)
    return False


def blocked_post(path: str) -> bool:
    return any(path.endswith(p) or path.startswith(p) for p in BLOCKED_POSTS)


# ---------------------------------------------------------------- sample data
# (name, category, serial, location path, status, country, region, city, site, tz, notes, simulated agent)
DEVICES = [
    ("pve-01", "Server", "SN-PVE-4471", "Home Lab / Rack A / U12", "active", "GB", "Greater Manchester", "Manchester", "Home lab",
     "Europe/London", "Proxmox host. 2x Xeon E5-2680 v4, 256 GB RAM.", {"cpu": 18, "cores": 28, "mem": (96, 256), "disk": (1.1, 3.6)}),
    ("pve-02", "Server", "SN-PVE-4472", "Home Lab / Rack A / U14", "active", "GB", "Greater Manchester", "Manchester", "Home lab",
     "Europe/London", "Proxmox host, HA pair with pve-01.", {"cpu": 9, "cores": 28, "mem": (61, 256), "disk": (0.8, 3.6)}),
    ("truenas", "Storage", "SN-TN-99812", "Home Lab / Rack A / U20", "active", "GB", "Greater Manchester", "Manchester", "Home lab",
     "Europe/London", "TrueNAS SCALE, 6x 8 TB RAIDZ2.", {"cpu": 4, "cores": 8, "mem": (28, 64), "disk": (31.2, 43.6)}),
    ("Ubiquiti EdgeSwitch 24", "Network", "UBNT-ES24-0193", "Home Lab / Rack A / U2", "active", "GB", "Greater Manchester", "Manchester", "Home lab",
     None, "Core switch. VLANs 10/20/30.", None),
    ("Raspberry Pi 5 (pihole)", "SBC", "RPI5-77120", "Home Lab / Shelf", "active", "GB", "Greater Manchester", "Manchester", "Home lab",
     None, "Pi-hole + Unbound.", {"cpu": 2, "cores": 4, "mem": (0.9, 8), "disk": (6.1, 59)}),
    ("ThinkPad T14 (loaner)", "Laptop", "PF-3XK2L9", "Office / Cupboard", "in repair", "GB", "Greater Manchester", "Manchester", "Office",
     None, "Keyboard replacement booked.", None),
    ("Dell PowerEdge R740", "Server", "DELL-R740-7Q2", "Uni Lab / Rack 3 / U8", "active", "GB", "West Yorkshire", "Leeds", "Uni research lab",
     "Europe/London", "Shared GPU box for the ML module.", {"cpu": 63, "cores": 40, "mem": (301, 384), "disk": (5.2, 7.0)}),
    ("Cisco Catalyst 2960", "Network", "FOC1234X0YZ", "Uni Lab / Rack 3 / U1", "active", "GB", "West Yorkshire", "Leeds", "Uni research lab",
     None, None, None),
    ("Fluke LinkIQ", "Tool", "FLK-LIQ-2231", "Uni Lab / Tool Cabinet", "active", "GB", "West Yorkshire", "Leeds", "Uni research lab",
     None, "Calibration due March.", None),
    ("edge-nyc-01", "Server", "HZ-NYC-55019", "Colo / NYC1 / Cage 4", "active", "US", "New York", "New York", "Equinix NY5",
     "America/New_York", "Edge cache. Maintenance only 02:00-05:00 local.", {"cpu": 22, "cores": 16, "mem": (40, 64), "disk": (0.4, 0.9)}),
    ("edge-dal-01", "Server", "HZ-DAL-55020", "Colo / DAL1 / Cage 2", "active", "US", "Texas", "Dallas", "Equinix DA6",
     "America/Chicago", "Edge cache.", {"cpu": 0, "cores": 16, "mem": (0, 64), "disk": (0.5, 0.9), "offline": True}),
    ("backup-fsn-01", "Storage", "HZ-FSN-80911", "Colo / FSN1", "active", "DE", "Saxony", "Falkenstein", "Hetzner FSN1",
     "Europe/Berlin", "Off-site backups (restic).", {"cpu": 3, "cores": 8, "mem": (9, 32), "disk": (13.9, 15.8)}),
    ("APC Smart-UPS 1500", "Power", "AS1234567890", "Home Lab / Rack A / U1", "active", "GB", "Greater Manchester", "Manchester", "Home lab",
     None, "Battery replaced 2025.", None),
    ("Old HP MicroServer", "Server", "HP-GEN8-0042", "Home Lab / Loft", "retired", "GB", "Greater Manchester", "Manchester", "Home lab",
     None, "Kept for spares.", None),
]


def _ensure_path(db: Session, path: str):
    from .models import Location
    parent, built = None, []
    for part in [p.strip() for p in path.split("/") if p.strip()]:
        built.append(part)
        q = db.query(Location).filter(Location.name == part,
                                      Location.parent_id.is_(None) if parent is None else Location.parent_id == parent.id)
        loc = q.first()
        if not loc:
            loc = Location(name=part, path=" / ".join(built), parent_id=parent.id if parent else None)
            db.add(loc)
            db.flush()
        parent = loc
    return parent


def _metrics(spec: dict, jitter: bool = True) -> dict:
    g = 1024 ** 3
    t = 1024 ** 4
    r = (lambda x, s: max(0.0, x + random.uniform(-s, s))) if jitter else (lambda x, s: x)
    cpu = min(100.0, r(spec["cpu"], 6))
    return {"agent_version": "0.2.0", "hostname": "", "os": "Debian GNU/Linux 12 (bookworm)", "kernel": "6.1.0-26-amd64",
            "cpu_count": spec["cores"], "cpu_percent": round(cpu, 1),
            "load": [round(cpu / 100 * spec["cores"] * f, 2) for f in (1, .9, .8)],
            "memory": {"total": int(spec["mem"][1] * g), "used": int(min(spec["mem"][1], r(spec["mem"][0], 1.5)) * g)},
            "disk": {"total": int(spec["disk"][1] * t), "used": int(spec["disk"][0] * t)},
            "uptime_s": int(86400 * random.uniform(3, 60))}


def reset(db: Session) -> dict:
    """Wipe everything and load the sample data. Only callable when demo mode is on."""
    if not ENABLED:
        raise RuntimeError("Refusing to reset: LAT_DEMO is not 1 (this would wipe real data).")
    from . import auth
    from .models import Asset, AuditLog, Maintenance, User
    db.execute(text("TRUNCATE audit_log, maintenance, assets, locations, user_sessions, users RESTART IDENTITY CASCADE"))
    db.add(User(email=DEMO_EMAIL, password_hash=auth.hash_password(secrets.token_urlsafe(32)), is_admin=True,
                timezone="Europe/London"))
    now = datetime.now(timezone.utc)
    random.seed(42)
    for i, (name, cat, serial, path, status, cc, region, city, site, tz, notes, sim) in enumerate(DEVICES):
        a = Asset(name=name, category=cat, serial_no=serial, status=status, location_id=_ensure_path(db, path).id,
                  country=cc, region=region, city=city, site=site, timezone=tz, notes=notes,
                  created_at=now - timedelta(days=120 - i * 5))
        if sim:
            a.agent_token_hash = f"{SIM_HASH_PREFIX}{i:02d}".ljust(64, "0")
            a.agent_metrics = {**_metrics(sim), "hostname": name.split(" ")[0]}
            a.agent_last_seen = now - (timedelta(hours=2, minutes=14) if sim.get("offline") else timedelta(seconds=10))
        db.add(a)
        db.flush()
        db.add(AuditLog(asset_id=a.id, action="CREATED", new_value=f"{name} added at {path}",
                        timestamp=a.created_at))
    by = {a.name: a for a in db.query(Asset).all()}
    history = [
        ("pve-02", "MOVE", "Home Lab / Rack A / U10", "Home Lab / Rack A / U14", 40),
        ("Dell PowerEdge R740", "EDITED", None, "Notes updated", 25),
        ("ThinkPad T14 (loaner)", "EDITED", None, "Status: active → in repair", 3),
        ("Old HP MicroServer", "EDITED", None, "Status: active → retired", 60),
        ("truenas", "EDITED", None, "Notes updated", 9),
    ]
    for name, action, old, new, days in history:
        db.add(AuditLog(asset_id=by[name].id, action=action, old_value=old, new_value=new,
                        timestamp=now - timedelta(days=days)))

    def window(name: str, days: int, start: str, hours: int, desc: str, status: str = "scheduled"):
        a = by[name]
        z = ZoneInfo(a.timezone or "Europe/London")
        d = (now.astimezone(z) + timedelta(days=days)).date()
        s = datetime.combine(d, datetime.strptime(start, "%H:%M").time(), tzinfo=z)
        db.add(Maintenance(asset_id=a.id, scheduled_date=s, end_date=s + timedelta(hours=hours), description=desc,
                           status=status, completion_date=s + timedelta(hours=hours) if status == "completed" else None))
    window("edge-nyc-01", 2, "02:00", 3, "Kernel update + reboot")
    window("pve-01", 5, "22:00", 4, "Proxmox 8.3 upgrade")
    window("truenas", 9, "01:00", 2, "Scrub + replace disk 4")
    window("backup-fsn-01", -6, "03:00", 1, "Restic prune", "completed")
    db.commit()
    return {"devices": len(DEVICES), "at": now.isoformat(timespec="seconds")}


def simulate(db: Session) -> int:
    """Give simulated agents a fresh heartbeat (skipping the one that is meant to be offline)."""
    from .models import Asset
    now = datetime.now(timezone.utc)
    specs = {d[0]: d[11] for d in DEVICES if d[11]}
    n = 0
    for a in db.query(Asset).filter(Asset.agent_token_hash.like(f"{SIM_HASH_PREFIX}%")).all():
        spec = specs.get(a.name)
        if not spec or spec.get("offline"):
            continue
        a.agent_metrics = {**_metrics(spec), "hostname": a.name.split(" ")[0]}
        a.agent_last_seen = now
        n += 1
    db.commit()
    return n


if __name__ == "__main__":
    from .database import SessionLocal
    if sys.argv[1:] != ["reset"]:
        sys.exit("usage: python -m app.demo reset")
    s = SessionLocal()
    try:
        print("[lat-demo] reset", reset(s), flush=True)
    finally:
        s.close()
