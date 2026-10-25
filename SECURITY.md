# Security policy

Please **don't open a public issue** for security problems.

Email **security@hallveckdigital.co.uk** with:
- what you found and how to reproduce it
- the version (`/health` shows it)

You'll get a reply within 3 working days. Fixes ship as a new release, with credit if you'd like it.

## Supported versions

Only the latest release gets security fixes. Update with `./latctl update`.

## Hardening notes for self-hosters

- Keep the app on your LAN or a VPN (for example Tailscale). Put HTTPS in front before exposing it publicly.
- The database is never published outside Docker, and its password is random and stored in `.env` (mode 600).
- The server agent is read-only. It never runs commands sent by the tracker.
