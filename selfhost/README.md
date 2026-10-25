# Lean Asset Tracker: Self-Hosted

Find any device in your lab in seconds: where it is, where it's been, and when it's next down for maintenance.

## Requirements
- Linux machine, VM, Raspberry Pi 4/5 (64-bit), or NAS that runs Docker
- Docker Engine with Compose v2 (`docker compose version` works)
- ~600 MB disk, ~300 MB RAM

## Install (about 3 minutes)
```bash
tar xzf lean-asset-tracker-*.tar.gz
cd lean-asset-tracker
./latctl install
```
The installer prints a **one-time setup link**. Open it on your phone or PC to create your admin account.
Lost it? `docker compose logs app | grep setup`.

Different port? Run `LAT_PORT=9000 ./latctl install`. Only this machine? Run `LAT_BIND=127.0.0.1 ./latctl install`.

## Day-to-day
| Command | What it does |
|---|---|
| `./latctl status` | Show what's running |
| `./latctl logs` | Follow app logs |
| `./latctl backup` | Save everything to `backups/lat-<date>.sql.gz` |
| `./latctl restore <file>` | Restore a backup (takes a safety backup first) |
| `./latctl update` | Backup, then rebuild with the new version |
| `./latctl stop` / `start` | Stop or start without losing data |
| `./latctl uninstall` | Stop (data kept). Add `--purge` to delete data too |

Tip: back up nightly with `crontab -e` → `0 3 * * * /path/to/lean-asset-tracker/latctl backup`

## Getting your data in and out
- **+ Add Asset**: name, category, serial, status, notes. Type a location like `Loft / Rack A / Shelf 2` and missing levels are created.
- **Import CSV** accepts messy headers: `Hardware`, `Device`, `Item` → name; `Type`, `Class` → category; `Serial`, `SN` → serial; `Room`, `Site`, `Where` → location (use `/` for levels).
- **Export CSV** downloads everything in the same format, so an export re-imports cleanly anywhere.

## Live server status (agent)
Open a server, tap **Connect this server**, and run the copied commands on that machine. It reports CPU, memory, disk, load and uptime every 30 s, and the dashboard shows **Online/Offline**.
The agent is a single Python file (stdlib only), runs as an unprivileged sandboxed systemd service, and is **read-only**: it never runs commands from the tracker.

## Security
- Login is required for everything except `/health` and the agent endpoint (which uses per-server tokens, stored hashed).
- Passwords are hashed with scrypt; sessions are HttpOnly cookies; repeated failed logins are rate-limited.
- Plain HTTP by default: keep it on your LAN or a VPN such as Tailscale. Don't port-forward it without HTTPS in front.
- The database isn't exposed outside Docker, and its password is random and stored in `.env` (mode 600).

## Your data is yours
Everything lives in a Docker volume on your machine. `./latctl backup` gives you a plain SQL dump you can read anywhere. No telemetry, no phoning home.

## Licence
AGPL-3.0-or-later. See `LICENSE`. If you run a modified version as a service for others, you must share your changes.
