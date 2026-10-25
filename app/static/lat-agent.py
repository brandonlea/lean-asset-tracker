#!/usr/bin/env python3
"""lat-agent: reports this server's health to Lean Asset Tracker.

Python 3.8+ standard library only. Read-only: it reports stats and never runs
commands it receives.

  LAT_URL=http://tracker:8080 LAT_TOKEN=lat_xxx python3 lat-agent.py          # run forever
  LAT_URL=... LAT_TOKEN=... python3 lat-agent.py --once                       # one report, for testing

SPDX-License-Identifier: AGPL-3.0-or-later
"""
import json
import os
import platform
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request

VERSION = "0.1.0"
INTERVAL = int(os.environ.get("LAT_INTERVAL", "30"))


def _cpu_times():
    with open("/proc/stat") as f:
        parts = [int(x) for x in f.readline().split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    return idle, sum(parts)


def cpu_percent(sample=1.0):
    i1, t1 = _cpu_times()
    time.sleep(sample)
    i2, t2 = _cpu_times()
    dt = t2 - t1
    return round(100.0 * (1 - (i2 - i1) / dt), 1) if dt else 0.0


def memory():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0]) * 1024
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    return {"total": total, "used": total - avail}


def os_name():
    try:
        with open("/etc/os-release") as f:
            kv = dict(l.rstrip("\n").split("=", 1) for l in f if "=" in l)
        return kv.get("PRETTY_NAME", "").strip('"') or platform.system()
    except OSError:
        return platform.system()


def collect():
    with open("/proc/uptime") as f:
        uptime = int(float(f.read().split()[0]))
    du = shutil.disk_usage("/")
    load = os.getloadavg()
    return {
        "agent_version": VERSION,
        "hostname": socket.gethostname(),
        "os": os_name(),
        "kernel": platform.release(),
        "cpu_count": os.cpu_count(),
        "cpu_percent": cpu_percent(),
        "load": [round(x, 2) for x in load],
        "memory": memory(),
        "disk": {"total": du.total, "used": du.used},
        "uptime_s": uptime,
    }


def send(url, token, payload):
    req = urllib.request.Request(
        url.rstrip("/") + "/api/agent/heartbeat",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                 "User-Agent": "lat-agent/" + VERSION},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def main():
    url, token = os.environ.get("LAT_URL"), os.environ.get("LAT_TOKEN")
    if not url or not token:
        sys.exit("Set LAT_URL and LAT_TOKEN (copy them from the device page in Lean Asset Tracker).")
    once = "--once" in sys.argv
    backoff = INTERVAL
    while True:
        try:
            reply = send(url, token, collect())
            print(f"[lat-agent] reported ok to {url}", flush=True)
            backoff = int(reply.get("interval", INTERVAL))
            if once:
                return 0
        except urllib.error.HTTPError as e:
            print(f"[lat-agent] server said {e.code}: {e.read()[:200]!r}", file=sys.stderr, flush=True)
            if e.code in (401, 403):
                if once:
                    return 2
                backoff = 300  # bad token: slow down, don't hammer
        except (urllib.error.URLError, OSError) as e:
            print(f"[lat-agent] can't reach {url}: {e}", file=sys.stderr, flush=True)
            if once:
                return 1
            backoff = min(backoff * 2, 300)
        time.sleep(backoff)


if __name__ == "__main__":
    sys.exit(main())
