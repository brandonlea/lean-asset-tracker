"""Security headers.

These are cheap, and they ship to every self-hosted install -- an operator who
never configures a reverse proxy still gets them. The point of pinning them in
a test is that headers are exactly the kind of thing that gets silently
dropped during a refactor and nobody notices for a year.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/unused")

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, follow_redirects=False)


@pytest.mark.parametrize("header,expected", [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
])
def test_headers_present_on_every_response(header, expected):
    r = client.get("/healthz")
    assert r.headers.get(header) == expected


def test_clickjacking_is_blocked_two_ways():
    """frame-ancestors for modern browsers, X-Frame-Options for old ones.
    An asset register framed inside an attacker page is a real click-to-delete
    risk."""
    r = client.get("/healthz")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"


def test_pages_are_not_cached():
    """Shared machine, browser back button after logout -- asset data should
    not still be sitting there."""
    assert client.get("/healthz").headers.get("Cache-Control") == "no-store"


def test_the_agent_script_is_still_cacheable():
    """The one asset this app serves itself. Everything else (Tailwind, htmx,
    fonts) currently comes from a CDN -- see test_csp_is_honest_about_the_cdns.
    There is no /static mount, so blanket no-store costs nothing today, but
    this guards the one file that would suffer from it."""
    r = client.get("/agent/lat-agent.py")
    assert r.status_code == 200
    assert r.headers.get("Cache-Control") != "no-store"


def test_csp_blocks_form_hijacking_and_base_tag_injection():
    csp = client.get("/healthz").headers["Content-Security-Policy"]
    assert "form-action 'self'" in csp      # stops creds POSTing offsite
    assert "base-uri 'self'" in csp         # stops <base> relative-URL hijack


def test_hsts_only_when_actually_on_https():
    """Sending HSTS over plain HTTP is meaningless, and on a Tailscale-only
    HTTP install it would lock the operator out of their own instance."""
    assert "Strict-Transport-Security" not in client.get("/healthz").headers
    r = client.get("/healthz", headers={"x-forwarded-proto": "https"})
    assert "max-age=31536000" in r.headers["Strict-Transport-Security"]


def test_csp_is_honest_about_the_cdns_it_still_uses():
    """Guard against a future 'tighten the CSP' commit that does not also
    vendor the assets -- that would break the UI for every user. When the CDNs
    are removed from the templates, this test should be flipped to assert they
    are ABSENT."""
    csp = client.get("/healthz").headers["Content-Security-Policy"]
    import pathlib
    tpl = " ".join(p.read_text() for p in pathlib.Path("app/templates").glob("*.html"))
    for host in ["cdn.tailwindcss.com", "unpkg.com"]:
        if host in tpl:
            assert host in csp, f"templates load {host} but the CSP forbids it"
