# SPDX-License-Identifier: AGPL-3.0-or-later
import csv
import io
from html import escape as html_escape
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import agent as ag
from . import auth
from .database import SessionLocal, engine, get_db
from .models import Asset, AuditLog, Base, Location, Maintenance, User
from . import demo
from . import devices as dv
from . import geo
from . import timezones as tzs
from .scheduling import WindowError, WindowView, build_window, humanise_duration, overlaps, window_state

VERSION = "0.5.1"
APP_DIR = Path(__file__).parent

app = FastAPI(title="Lean Asset Tracker", version=VERSION)
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.globals.update(human_bytes=ag.human_bytes, human_uptime=ag.human_uptime, pct=ag.pct,
                             fmt_dt=tzs.fmt, tz_label=tzs.label, flag=geo.flag, country_name=geo.name,
                             site_summary=geo.summary, device_code=dv.device_code, demo_mode=demo.ENABLED,
                             demo_note=demo.RESET_NOTE)


# ---------------------------------------------------------------- startup / migrations
@app.on_event("startup")
def startup():
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    # create_all never alters existing tables, so add new columns explicitly (idempotent)
    with engine.connect() as conn:
        for stmt in (
            "ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS end_date TIMESTAMPTZ",
            "ALTER TABLE assets ADD COLUMN IF NOT EXISTS notes TEXT",
            "ALTER TABLE assets ADD COLUMN IF NOT EXISTS agent_token_hash VARCHAR(64)",
            "ALTER TABLE assets ADD COLUMN IF NOT EXISTS agent_last_seen TIMESTAMPTZ",
            "ALTER TABLE assets ADD COLUMN IF NOT EXISTS agent_metrics JSON",
            "ALTER TABLE assets ADD COLUMN IF NOT EXISTS timezone VARCHAR(64)",
            "ALTER TABLE assets ADD COLUMN IF NOT EXISTS country VARCHAR(2)",
            "ALTER TABLE assets ADD COLUMN IF NOT EXISTS region VARCHAR(120)",
            "ALTER TABLE assets ADD COLUMN IF NOT EXISTS city VARCHAR(120)",
            "ALTER TABLE assets ADD COLUMN IF NOT EXISTS site VARCHAR(120)",
            "CREATE INDEX IF NOT EXISTS ix_assets_country ON assets (country)",
            "ALTER TABLE assets ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
            "CREATE INDEX IF NOT EXISTS ix_assets_archived_at ON assets (archived_at)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/London'",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_assets_agent_token_hash ON assets (agent_token_hash)",
        ):
            conn.execute(text(stmt))
        conn.commit()
    db = SessionLocal()
    try:
        if demo.ENABLED:
            if db.query(User).count() == 0:
                print("[lat-demo] seeding", demo.reset(db), flush=True)
        elif db.query(User).count() == 0:
            print(f"[lat] No users yet. Create the admin account at: /setup?token={auth.SETUP_TOKEN}", flush=True)
    finally:
        db.close()
    if demo.ENABLED:
        import asyncio
        asyncio.get_event_loop().create_task(_demo_heartbeats())


async def _demo_heartbeats():
    """Demo only: keep the simulated servers 'online' with fresh, slightly varying stats."""
    import asyncio
    while True:
        try:
            db = SessionLocal()
            try:
                demo.simulate(db)
            finally:
                db.close()
        except Exception as e:                      # never kill the loop; the demo just goes stale
            print(f"[lat-demo] simulate failed: {e!r}", flush=True)
        await asyncio.sleep(20)


# ---------------------------------------------------------------- auth middleware
@app.middleware("http")
async def https_redirect(request: Request, call_next):
    """Behind Cloudflare with an https LAT_PUBLIC_URL, send plain-HTTP visitors to HTTPS."""
    import os
    if (os.environ.get("LAT_PUBLIC_URL", "").startswith("https://")
            and '"scheme":"http"' in request.headers.get("cf-visitor", "").replace(" ", "")):
        return RedirectResponse(str(request.url.replace(scheme="https", port=None)), status_code=301)
    return await call_next(request)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if demo.ENABLED:
        return await _demo_gate(request, call_next)
    if path.startswith(auth.PUBLIC_PREFIXES):
        return await call_next(request)
    db = SessionLocal()
    try:
        user = auth.user_from_token(db, request.cookies.get(auth.COOKIE))
        if user:
            request.state.user_email = user.email
            request.state.user_id = user.id
            request.state.user_tz = tzs.valid_tz(user.timezone) or tzs.DEFAULT_TZ
            return await call_next(request)
        needs_setup = db.query(User).count() == 0
    finally:
        db.close()
    target = "/setup" if needs_setup else "/login"
    if not needs_setup and request.method == "GET" and not request.headers.get("HX-Request"):
        from urllib.parse import quote
        wanted = path + (f"?{request.url.query}" if request.url.query else "")
        if wanted != "/":
            target += "?next=" + quote(dv.safe_next(wanted), safe="")
    if request.headers.get("HX-Request"):
        return Response(status_code=401, headers={"HX-Redirect": target})
    if path.startswith(("/api/", "/assets/", "/export/", "/import/")) and "text/html" not in request.headers.get("accept", ""):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return RedirectResponse(target, status_code=303)


async def _demo_gate(request: Request, call_next):
    """Demo mode: everyone is the demo user; risky actions are blocked and writes are rate-limited."""
    path, method = request.url.path, request.method
    if path in ("/login", "/setup"):
        return RedirectResponse("/", status_code=303)
    ip = request.headers.get("cf-connecting-ip") or _client_ip(request)
    if method == "POST":
        if demo.blocked_post(path):
            return _demo_refusal(request, "That's switched off in the demo. Install it free to try it for real.")
        if demo.write_limited(ip):
            return _demo_refusal(request, "Easy there! The demo allows 60 changes per 10 minutes.", 429)
        if path == "/import/csv" and int(request.headers.get("content-length") or 0) > demo.MAX_IMPORT_BYTES:
            return _demo_refusal(request, "Demo imports are limited to 64 KB.", 413)
        if path == "/assets/new":
            db = SessionLocal()
            try:
                full = db.query(Asset).count() >= demo.MAX_DEVICES
            finally:
                db.close()
            if full:
                return _demo_refusal(request, "The demo is full for today. It resets tonight.")
    db = SessionLocal()
    try:
        user = db.query(User).order_by(User.id).first()
        if not user:
            return JSONResponse({"detail": "Demo is resetting, try again in a moment."}, status_code=503)
        request.state.user_email, request.state.user_id = "Demo user", user.id
        request.state.user_tz = tzs.valid_tz(request.cookies.get("lat_demo_tz")) or tzs.DEFAULT_TZ
    finally:
        db.close()
    return await call_next(request)


def _demo_refusal(request: Request, msg: str, code: int = 403):
    if request.headers.get("HX-Request"):
        # 200 so HTMX swaps it in; shown as a toast over the current view
        resp = HTMLResponse(f'<div class="fixed bottom-4 inset-x-4 sm:left-auto sm:right-4 sm:w-96 z-50 text-sm px-4 py-3 '
                            f'rounded-lg bg-amber-500/15 ring-1 ring-amber-500/40 text-amber-100" role="alert" '
                            f'onclick="this.remove()">{html_escape(msg)} Tap to dismiss.</div>')
        resp.headers["HX-Retarget"] = "#demo-toast"
        resp.headers["HX-Reswap"] = "innerHTML"
        return resp
    if "text/html" in (request.headers.get("accept") or ""):      # plain form posts (CSV import)
        from urllib.parse import quote
        return RedirectResponse(f"/?import_error={quote(msg)}", status_code=303)
    return JSONResponse({"detail": msg}, status_code=code)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_session_cookie(resp: Response, request: Request, token: str):
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=secure, path="/")


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    if db.query(User).count():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("setup.html", {"request": request, "token": token})


@app.post("/setup", response_class=HTMLResponse)
def setup_submit(request: Request, token: str = Form(""), email: str = Form(...), password: str = Form(...),
                 confirm: str = Form(...), timezone_name: str = Form("", alias="timezone"), db: Session = Depends(get_db)):
    if db.query(User).count():
        return RedirectResponse("/login", status_code=303)
    ctx = {"request": request, "token": token, "email": email}
    import hmac
    if not hmac.compare_digest(token, auth.SETUP_TOKEN):
        return templates.TemplateResponse("setup.html", {**ctx, "error": "Setup link is invalid. Use the link from the install output or `docker logs`."}, status_code=403)
    email = email.strip().lower()
    if "@" not in email or len(email) > 254:
        return templates.TemplateResponse("setup.html", {**ctx, "error": "Enter a valid email."}, status_code=400)
    if password != confirm:
        return templates.TemplateResponse("setup.html", {**ctx, "error": "Passwords don't match."}, status_code=400)
    if problem := auth.password_problem(password):
        return templates.TemplateResponse("setup.html", {**ctx, "error": problem}, status_code=400)
    user = User(email=email, password_hash=auth.hash_password(password), is_admin=True,
                timezone=tzs.valid_tz(timezone_name) or tzs.DEFAULT_TZ)
    db.add(user)
    db.commit()
    resp = RedirectResponse("/", status_code=303)
    _set_session_cookie(resp, request, auth.create_session(db, user))
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "", db: Session = Depends(get_db)):
    if db.query(User).count() == 0:
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "next": dv.safe_next(next)})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form(""),
                 db: Session = Depends(get_db)):
    ip = _client_ip(request)
    next = dv.safe_next(next)
    if wait := auth.throttle.blocked(ip):
        return templates.TemplateResponse("login.html", {"request": request, "email": email, "next": next,
                                          "error": f"Too many attempts. Try again in {wait // 60 + 1} min."}, status_code=429)
    user = auth.authenticate(db, email, password)
    if not user:
        auth.throttle.fail(ip)
        return templates.TemplateResponse("login.html", {"request": request, "email": email, "next": next,
                                          "error": "Email or password is wrong."}, status_code=401)
    auth.throttle.reset(ip)
    resp = RedirectResponse(next, status_code=303)
    _set_session_cookie(resp, request, auth.create_session(db, user))
    return resp


@app.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    auth.destroy_session(db, request.cookies.get(auth.COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


# ---------------------------------------------------------------- account settings
def _settings_panel(request: Request, **extra):
    return templates.TemplateResponse("_settings.html", {
        "request": request, "user_tz": user_tz_name(request), "tz_choices": tzs.choices(),
        "user_email": getattr(request.state, "user_email", ""), **extra})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return _settings_panel(request)


@app.post("/settings", response_class=HTMLResponse)
def settings_save(request: Request, timezone_name: str = Form("", alias="timezone"), db: Session = Depends(get_db)):
    new = tzs.valid_tz(timezone_name)
    if not new:
        return _settings_panel(request, error="Pick a timezone from the list.")
    user = db.query(User).filter(User.id == request.state.user_id).first()
    user.timezone = new
    db.commit()
    request.state.user_tz = new
    resp = _settings_panel(request, flash=f"Saved. Times now show in {new}.")
    resp.headers["HX-Trigger"] = "tzChanged"
    return resp


# ---------------------------------------------------------------- helpers
def location_path(loc: Optional[Location]) -> str:
    parts, seen = [], set()
    while loc is not None and loc.id not in seen:
        seen.add(loc.id)
        parts.append(loc.name)
        loc = loc.parent
    return " / ".join(reversed(parts))


def ensure_location_path(db: Session, raw: str) -> Optional[int]:
    """'Loft / Rack A / Shelf 2' -> id of Shelf 2, creating any missing levels."""
    parts = [p.strip() for p in (raw or "").replace("\\", "/").split("/") if p.strip()]
    parent_id = None
    for i, name in enumerate(parts):
        q = db.query(Location).filter(Location.name == name[:120])
        q = q.filter(Location.parent_id.is_(None)) if parent_id is None else q.filter(Location.parent_id == parent_id)
        loc = q.order_by(Location.id).first()
        if not loc:
            loc = Location(name=name[:120], path="/".join(parts[: i + 1]), parent_id=parent_id)
            db.add(loc)
            db.flush()
        parent_id = loc.id
    return parent_id


def user_tz_name(request: Request) -> str:
    return getattr(request.state, "user_tz", tzs.DEFAULT_TZ)


def asset_tz_name(request: Request, asset: Asset) -> str:
    """The server's own timezone, or the viewer's when the server has none set."""
    return tzs.valid_tz(asset.timezone) or user_tz_name(request)


def tz_choices_for(cc: str | None) -> list:
    """Timezone <select> groups with the country's own zones first (each zone listed once)."""
    own = geo.zones_for(cc)
    if not own:
        return tzs.choices()
    first = [(z, f"{tzs.label(z)}" + (f" · {note}" if note else "")) for z, note in own]
    taken = {z for z, _ in own}
    rest = [(g, [(v, t) for v, t in opts if v not in taken]) for g, opts in tzs.choices()]
    return [(f"In {geo.name(cc)}", first)] + [(g, o) for g, o in rest if o]


def _site_form_values(country: str, region: str, city: str, site: str) -> dict:
    return {"country": geo.valid_country(country), "region": geo.clean_text(region),
            "city": geo.clean_text(city), "site": geo.clean_text(site)}


def _card(asset: Asset) -> dict:
    place = asset.city or geo.name(asset.country) or ""
    return {"a": asset, "loc": location_path(asset.location) or "No location",
            "place": (geo.flag(asset.country) + " " + place).strip() if (asset.country or asset.city) else "",
            "agent": ag.status(asset.agent_last_seen) if asset.agent_token_hash else None}


def _render_cards(request: Request, assets, q: str = "", archived: bool = False):
    return templates.TemplateResponse("_cards.html", {"request": request, "cards": [_card(a) for a in assets], "q": q,
                                      "archived": archived})


# ---------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def index(request: Request, imported: Optional[int] = None, skipped: Optional[int] = None, import_error: str = "",
          asset: Optional[int] = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse("index.html", {
        "request": request, "user_email": getattr(request.state, "user_email", ""), "user_tz": user_tz_name(request),
        "imported": imported, "skipped": skipped, "import_error": import_error[:200], "version": VERSION,
        "open_asset": asset if asset and db.query(Asset.id).filter(Asset.id == asset).first() else None,
        "archived_count": db.query(Asset).filter(Asset.archived_at.isnot(None)).count()})


@app.get("/search", response_class=HTMLResponse)
def search_assets(request: Request, q: str = "", archived: str = "", db: Session = Depends(get_db)):
    show_archived = archived == "1"
    like = f"%{q.strip()}%"
    cond = (Asset.name.ilike(like) | Asset.serial_no.ilike(like) | Asset.category.ilike(like)
            | Location.name.ilike(like) | Asset.notes.ilike(like)
            | Asset.region.ilike(like) | Asset.city.ilike(like) | Asset.site.ilike(like))
    if ccs := geo.codes_matching(q):          # "germany", "uk", "de" -> servers in that country
        cond = cond | Asset.country.in_(ccs)
    cond = cond & (Asset.archived_at.isnot(None) if show_archived else Asset.archived_at.is_(None))
    assets = (db.query(Asset).outerjoin(Location)
              .filter(cond)
              .order_by(Asset.name).limit(200).all())
    return _render_cards(request, assets, q, show_archived)


# ---------------------------------------------------------------- add asset
STATUSES = ["active", "spare", "in repair", "retired"]


def _asset_form(request: Request, db: Session, form: dict, error: str = "", status_code: int = 200,
                asset_id: Optional[int] = None):
    # HTMX discards 4xx/5xx bodies by default, so validation errors for HTMX requests must be 200
    # or the user never sees the message. Plain (non-HTMX) clients still get the real status.
    if request.headers.get("HX-Request"):
        status_code = 200
    paths = sorted({location_path(l) for l in db.query(Location).all()} - {""})
    return templates.TemplateResponse("_asset_form.html", {"request": request, "f": form, "error": error,
                                      "statuses": STATUSES, "paths": paths, "tz_choices": tz_choices_for(form.get("country")),
                                      "countries": geo.countries(), "edit_id": asset_id,
                                      "user_tz": user_tz_name(request)}, status_code=status_code)


def _form_from_asset(a: Asset) -> dict:
    return dict(name=a.name, category=a.category, serial_no=a.serial_no or "", location=location_path(a.location),
                status=a.status, notes=a.notes or "", timezone=a.timezone or "", country=a.country or "",
                region=a.region or "", city=a.city or "", site=a.site or "")


def _clean_asset_form(db: Session, f: dict, exclude_id: Optional[int] = None) -> tuple[Optional[dict], str, int]:
    """Validate the Add/Edit form. Returns (clean values, error, status)."""
    if f["country"] and not geo.valid_country(f["country"]):
        return None, "Pick a country from the list.", 422
    if f["timezone"] and not tzs.valid_tz(f["timezone"]):
        return None, "Pick a timezone from the list.", 422
    if not f["name"]:
        return None, "Give the device a name.", 422
    if not f["category"]:
        return None, "Pick or type a category.", 422
    if f["status"] not in STATUSES:
        f["status"] = "active"
    if f["serial_no"]:
        q = db.query(Asset).filter(Asset.serial_no == f["serial_no"][:120])
        if exclude_id is not None:
            q = q.filter(Asset.id != exclude_id)
        if q.first():
            return None, f"Serial {f['serial_no']} is already on another device.", 409
    clean = dict(name=f["name"][:200], category=f["category"][:80], serial_no=f["serial_no"][:120] or None,
                 status=f["status"], notes=f["notes"][:4000] or None, timezone=tzs.valid_tz(f["timezone"]),
                 **_site_form_values(f["country"], f["region"], f["city"], f["site"]))
    return clean, "", 200


def _read_form(name, category, serial_no, location, status, notes, timezone_name, country, region, city, site) -> dict:
    return dict(name=name.strip(), category=category.strip(), serial_no=serial_no.strip(), location=location.strip(),
                status=status, notes=notes.strip(), timezone=timezone_name.strip(), country=country.strip(),
                region=region, city=city, site=site)


@app.get("/assets/new", response_class=HTMLResponse)
def new_asset_form(request: Request, db: Session = Depends(get_db)):
    return _asset_form(request, db, {"status": "active"})


@app.post("/assets/new", response_class=HTMLResponse)
def create_asset_form(request: Request, name: str = Form(""), category: str = Form(""), serial_no: str = Form(""),
                      location: str = Form(""), status: str = Form("active"), notes: str = Form(""),
                      timezone_name: str = Form("", alias="timezone"), country: str = Form(""), region: str = Form(""),
                      city: str = Form(""), site: str = Form(""), db: Session = Depends(get_db)):
    f = _read_form(name, category, serial_no, location, status, notes, timezone_name, country, region, city, site)
    clean, error, code = _clean_asset_form(db, f)
    if error:
        return _asset_form(request, db, f, error, code)
    asset = Asset(**clean, location_id=ensure_location_path(db, f["location"]))
    db.add(asset)
    db.flush()
    where = geo.summary(asset.country, asset.region, asset.city, asset.site)
    db.add(AuditLog(asset_id=asset.id, action="CREATED", new_value=f"{asset.name} added" +
                    (f" at {f['location']}" if f["location"] else "") + (f" ({where})" if where else "")))
    db.commit()
    resp = asset_detail(request, asset.id, db)
    resp.headers["HX-Trigger"] = "assetsChanged"
    return resp


@app.get("/assets/{asset_id}/edit", response_class=HTMLResponse)
def edit_asset_form(request: Request, asset_id: int, db: Session = Depends(get_db)):
    return _asset_form(request, db, _form_from_asset(_get_asset(db, asset_id)), asset_id=asset_id)


@app.post("/assets/{asset_id}/edit", response_class=HTMLResponse)
def update_asset(request: Request, asset_id: int, name: str = Form(""), category: str = Form(""),
                 serial_no: str = Form(""), location: str = Form(""), status: str = Form("active"),
                 notes: str = Form(""), timezone_name: str = Form("", alias="timezone"), country: str = Form(""),
                 region: str = Form(""), city: str = Form(""), site: str = Form(""), db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    f = _read_form(name, category, serial_no, location, status, notes, timezone_name, country, region, city, site)
    clean, error, code = _clean_asset_form(db, f, exclude_id=asset_id)
    if error:
        return _asset_form(request, db, f, error, code, asset_id=asset_id)
    before = {k: getattr(asset, k) for k in dv.EDIT_FIELDS}
    old_loc = location_path(asset.location)
    for k, v in clean.items():
        setattr(asset, k, v)
    changes = dv.diff_fields(before, clean)
    if changes:
        db.add(AuditLog(asset_id=asset_id, action="EDITED", new_value="; ".join(changes)[:2000]))
    new_loc_id = ensure_location_path(db, f["location"])
    moved = new_loc_id != asset.location_id
    if moved:
        asset.location_id = new_loc_id
        db.flush()
        db.refresh(asset)
        db.add(AuditLog(asset_id=asset_id, action="MOVE", old_value=old_loc or "No location",
                        new_value=location_path(asset.location) or "No location"))
    db.commit()
    resp = asset_detail(request, asset_id, db, flash="Saved." if (changes or moved) else "No changes.")
    resp.headers["HX-Trigger"] = "assetsChanged"
    return resp


@app.post("/assets/{asset_id}/archive", response_class=HTMLResponse)
def archive_asset(request: Request, asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    if asset.archived_at is None:
        asset.archived_at = datetime.now(timezone.utc)
        notes = ["Archived"]
        if asset.agent_token_hash:                 # an archived server must not keep reporting in
            asset.agent_token_hash = asset.agent_last_seen = asset.agent_metrics = None
            notes.append("agent disconnected")
        upcoming = (db.query(Maintenance).filter(Maintenance.asset_id == asset_id, Maintenance.status == "scheduled",
                                                 Maintenance.scheduled_date > asset.archived_at).all())
        for w in upcoming:
            w.status = "cancelled"
        if upcoming:
            notes.append(f"{len(upcoming)} upcoming maintenance window{'s' if len(upcoming) != 1 else ''} cancelled")
        db.add(AuditLog(asset_id=asset_id, action="ARCHIVED", new_value=", ".join(notes)))
        db.commit()
    resp = asset_detail(request, asset_id, db)
    resp.headers["HX-Trigger"] = "assetsChanged"
    return resp


@app.post("/assets/{asset_id}/restore", response_class=HTMLResponse)
def restore_asset(request: Request, asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    if asset.archived_at is not None:
        asset.archived_at = None
        db.add(AuditLog(asset_id=asset_id, action="RESTORED", new_value="Restored from archive"))
        db.commit()
    resp = asset_detail(request, asset_id, db)
    resp.headers["HX-Trigger"] = "assetsChanged"
    return resp


@app.post("/assets/{asset_id}/delete", response_class=HTMLResponse)
def delete_asset(request: Request, asset_id: int, confirm_name: str = Form(""), db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    if asset.archived_at is None:
        return asset_detail(request, asset_id, db, error="Archive the device first, then it can be deleted.")
    if confirm_name.strip() != asset.name:
        return asset_detail(request, asset_id, db, error="Type the device name exactly to delete it.")
    name = asset.name
    db.query(Maintenance).filter(Maintenance.asset_id == asset_id).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.asset_id == asset_id).delete(synchronize_session=False)
    db.delete(asset)
    db.commit()
    print(f"[lat] {getattr(request.state, 'user_email', '?')} permanently deleted asset {asset_id} ({name!r})", flush=True)
    resp = HTMLResponse(f'<div class="fixed bottom-4 inset-x-4 sm:left-auto sm:right-4 sm:w-96 z-50 text-sm px-4 py-3 rounded-lg '
                        f'bg-slate-800 ring-1 ring-slate-600 text-slate-200" role="status" '
                        f'onclick="this.remove()">Deleted {html_escape(name)}. Tap to dismiss.</div>')
    resp.headers["HX-Trigger"] = "assetsChanged"
    return resp


# ---------------------------------------------------------------- QR codes + labels
def public_base(request: Request) -> str:
    """Base URL printed into QR codes. LAT_PUBLIC_URL wins so labels survive a proxy/hostname change."""
    import os
    return (os.environ.get("LAT_PUBLIC_URL") or str(request.base_url)).rstrip("/")


@app.get("/a/{asset_id}")
def short_link(asset_id: int):
    """Target of the QR code: opens the device panel on the dashboard."""
    return RedirectResponse(f"/?asset={asset_id}", status_code=303)


@app.get("/assets/{asset_id}/qr.svg")
def asset_qr(request: Request, asset_id: int, db: Session = Depends(get_db)):
    _get_asset(db, asset_id)
    return Response(dv.qr_svg(f"{public_base(request)}/a/{asset_id}", scale=8), media_type="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=86400"})


@app.get("/labels", response_class=HTMLResponse)
def labels(request: Request, ids: list[int] = Query([]), size: str = "medium", db: Session = Depends(get_db)):
    sheet = dv.SHEETS.get(size, dv.SHEETS["medium"])
    base = public_base(request)
    chosen = []
    if ids:
        by_id = {a.id: a for a in db.query(Asset).filter(Asset.id.in_(ids[:500])).all()}
        chosen = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
    items = [{"a": a, "code": dv.device_code(a.id), "qr": dv.qr_svg(f"{base}/a/{a.id}"),
              "where": a.city or geo.name(a.country) or location_path(a.location)} for a in chosen]
    all_assets = db.query(Asset).filter(Asset.archived_at.is_(None)).order_by(Asset.name).all()
    return templates.TemplateResponse("labels.html", {
        "request": request, "sheet": sheet, "sheets": dv.SHEETS, "pages": dv.pages(items, sheet), "count": len(items),
        "selected": set(ids), "all_assets": all_assets, "loc": location_path, "base": base})


# ---------------------------------------------------------------- asset detail + maintenance
def _window_views(db: Session, asset_id: int, server_tz: str = tzs.DEFAULT_TZ,
                  viewer_tz: str = tzs.DEFAULT_TZ) -> list[WindowView]:
    """Windows in the server's local time, plus the viewer's time when the two zones differ."""
    now = datetime.now(timezone.utc)
    stz, vtz = tzs.get_tz(server_tz), tzs.get_tz(viewer_tz)
    rows = (db.query(Maintenance).filter(Maintenance.asset_id == asset_id)
            .order_by(Maintenance.scheduled_date.asc()).all())
    views = []
    for r in rows:
        start = r.scheduled_date.astimezone(stz)
        end = (r.end_date or r.scheduled_date + timedelta(hours=1)).astimezone(stz)
        differs = start.utcoffset() != start.astimezone(vtz).utcoffset()  # same clock = no noise
        views.append(WindowView(id=r.id, description=r.description, start=start, end=end,
                                state=window_state(start, end, r.status, now),
                                duration=humanise_duration(start, end), crosses_midnight=start.date() != end.date(),
                                user_start=start.astimezone(vtz) if differs else None,
                                user_end=end.astimezone(vtz) if differs else None))
    order = {"in_progress": 0, "overdue": 1, "upcoming": 2, "completed": 3, "cancelled": 4}
    views.sort(key=lambda v: (order[v.state], v.start))
    return views


def _tz_ctx(request: Request, asset: Asset) -> dict:
    s, u = asset_tz_name(request, asset), user_tz_name(request)
    return {"server_tz": s, "user_tz": u, "server_tz_own": bool(tzs.valid_tz(asset.timezone)),
            "server_now": datetime.now(tzs.get_tz(s)), "tzinfo_user": tzs.get_tz(u)}


def _windows_for(request: Request, db: Session, asset: Asset) -> list[WindowView]:
    return _window_views(db, asset.id, asset_tz_name(request, asset), user_tz_name(request))


def _maintenance_section(request: Request, db: Session, asset_id: int, **extra):
    asset = _get_asset(db, asset_id)
    return templates.TemplateResponse("_maintenance.html", {
        "request": request, "asset_id": asset_id, "windows": _windows_for(request, db, asset),
        **_tz_ctx(request, asset), **extra})


def _agent_ctx(request: Request, asset: Asset, **extra) -> dict:
    return {"request": request, "asset": asset, "agent_status": ag.status(asset.agent_last_seen),
            "agent_ago": ag.ago(asset.agent_last_seen), "m": asset.agent_metrics or {},
            "base_url": str(request.base_url), **extra}


def _get_asset(db: Session, asset_id: int) -> Asset:
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(404, "Asset not found")
    return asset


@app.get("/assets/{asset_id}/details", response_class=HTMLResponse)
def asset_detail_route(request: Request, asset_id: int, db: Session = Depends(get_db)):
    return asset_detail(request, asset_id, db)


def asset_detail(request: Request, asset_id: int, db: Session, flash: str = "", error: str = ""):
    """Render the device panel. Not a route itself, so flash/error can't be spoofed via the query string."""
    asset = _get_asset(db, asset_id)
    logs = db.query(AuditLog).filter(AuditLog.asset_id == asset_id).order_by(AuditLog.timestamp.desc()).limit(50).all()
    return templates.TemplateResponse("asset_detail.html", {
        **_agent_ctx(request, asset), **_tz_ctx(request, asset), "logs": logs, "loc": location_path(asset.location),
        "asset_id": asset_id, "windows": _windows_for(request, db, asset),
        "tz_choices": tz_choices_for(asset.country), "countries": geo.countries(),
        "flash": flash, "error": error, "code": dv.device_code(asset.id)})


@app.get("/geo/zones", response_class=HTMLResponse)
def geo_zones(request: Request, country: str = "", timezone_name: str = Query("", alias="timezone")):
    """Re-render the timezone picker for a country, pre-selecting its zone when there is only one."""
    cc = geo.valid_country(country)
    picked = geo.suggested_tz(cc, tzs.valid_tz(timezone_name)) or tzs.valid_tz(timezone_name)
    return templates.TemplateResponse("_tz_field.html", {
        "request": request, "tz_choices": tz_choices_for(cc), "selected": picked, "user_tz": user_tz_name(request),
        "hint": (f"Set from {geo.name(cc)}" if picked and picked != tzs.valid_tz(timezone_name)
                 else (f"{geo.name(cc)} has {len(geo.zones_for(cc))} timezones, pick the one for this city"
                       if len(geo.zones_for(cc)) > 1 and not picked else ""))})


@app.post("/assets/{asset_id}/site", response_class=HTMLResponse)
def set_asset_site(request: Request, asset_id: int, country: str = Form(""), region: str = Form(""),
                   city: str = Form(""), site: str = Form(""), timezone_name: str = Form("", alias="timezone"),
                   db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    if country.strip() and not geo.valid_country(country):
        raise HTTPException(422, "Unknown country")
    if timezone_name.strip() and not tzs.valid_tz(timezone_name):
        raise HTTPException(422, "Unknown timezone")
    new = _site_form_values(country, region, city, site)
    old_where = geo.summary(asset.country, asset.region, asset.city, asset.site)
    for k, v in new.items():
        setattr(asset, k, v)
    new_where = geo.summary(asset.country, asset.region, asset.city, asset.site)
    if new_where != old_where:
        db.add(AuditLog(asset_id=asset_id, action="SITE_CHANGED", old_value=old_where or "Not set",
                        new_value=new_where or "Not set"))
    new_tz = tzs.valid_tz(timezone_name)
    if new_tz != asset.timezone:
        db.add(AuditLog(asset_id=asset_id, action="TIMEZONE_CHANGED", old_value=asset.timezone or "Owner's timezone",
                        new_value=new_tz or "Owner's timezone"))
        asset.timezone = new_tz
    db.commit()
    return asset_detail(request, asset_id, db)


@app.post("/assets/{asset_id}/timezone", response_class=HTMLResponse)
def set_asset_timezone(request: Request, asset_id: int, timezone_name: str = Form("", alias="timezone"),
                       db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    new = tzs.valid_tz(timezone_name)
    if timezone_name.strip() and not new:
        raise HTTPException(422, "Unknown timezone")
    if new != asset.timezone:
        db.add(AuditLog(asset_id=asset_id, action="TIMEZONE_CHANGED", old_value=asset.timezone or "Owner's timezone",
                        new_value=new or "Owner's timezone"))
        asset.timezone = new
        db.commit()
    return asset_detail(request, asset_id, db)


@app.get("/assets/{asset_id}/maintenance", response_class=HTMLResponse)
def maintenance_section(request: Request, asset_id: int, db: Session = Depends(get_db)):
    return _maintenance_section(request, db, asset_id)


@app.get("/assets/{asset_id}/maintenance/form", response_class=HTMLResponse)
def get_maintenance_form(request: Request, asset_id: int, db: Session = Depends(get_db)):
    today = datetime.now(tzs.get_tz(asset_tz_name(request, _get_asset(db, asset_id)))).date().isoformat()
    return _maintenance_section(request, db, asset_id, show_form=True,
                                form={"date": today, "start": "02:00", "end": "05:00", "description": ""})


@app.post("/assets/{asset_id}/maintenance", response_class=HTMLResponse)
def add_maintenance(request: Request, asset_id: int, description: str = Form(...), date: str = Form(...),
                    start_time: str = Form(...), end_time: str = Form(...), db: Session = Depends(get_db)):
    form = {"date": date, "start": start_time, "end": end_time, "description": description}
    asset = _get_asset(db, asset_id)
    server_tz = asset_tz_name(request, asset)
    description = description.strip()
    if not description:
        return _maintenance_section(request, db, asset_id, show_form=True, form=form, error="Add a short description of the work.")
    try:
        start, end = build_window(date, start_time, end_time, tzs.get_tz(server_tz))
    except WindowError as e:
        return _maintenance_section(request, db, asset_id, show_form=True, form=form, error=str(e))
    if end <= datetime.now(timezone.utc):
        return _maintenance_section(request, db, asset_id, show_form=True, form=form, error="That window is already in the past.")
    clash = next((w for w in _windows_for(request, db, asset)
                  if w.state in ("upcoming", "in_progress") and overlaps(start, end, w.start, w.end)), None)
    if clash:
        msg = f"Clashes with \"{clash.description}\" ({clash.start:%a %d %b %H:%M}\u2013{clash.end:%H:%M})."
        return _maintenance_section(request, db, asset_id, show_form=True, form=form, error=msg)
    db.add(Maintenance(asset_id=asset_id, description=description[:200], scheduled_date=start, end_date=end))
    db.add(AuditLog(asset_id=asset_id, action="MAINTENANCE_SCHEDULED",
                    new_value=f"{description[:200]}: {start:%d %b %Y %H:%M}\u2013{end:%H:%M} {end:%Z} ({server_tz})"))
    db.commit()
    return _maintenance_section(request, db, asset_id, flash="Maintenance window scheduled.")


@app.post("/maintenance/{mid}/{action}", response_class=HTMLResponse)
def update_maintenance(request: Request, mid: int, action: str, db: Session = Depends(get_db)):
    if action not in ("complete", "cancel"):
        raise HTTPException(400, "Unknown action")
    m = db.query(Maintenance).filter(Maintenance.id == mid).first()
    if not m:
        raise HTTPException(404, "Maintenance window not found")
    if m.status == "scheduled":
        m.status = "completed" if action == "complete" else "cancelled"
        if action == "complete":
            m.completion_date = datetime.now(timezone.utc)
        db.add(AuditLog(asset_id=m.asset_id, action=f"MAINTENANCE_{m.status.upper()}", new_value=m.description))
        db.commit()
    return _maintenance_section(request, db, m.asset_id)


# ---------------------------------------------------------------- server agent
@app.get("/assets/{asset_id}/agent", response_class=HTMLResponse)
def agent_section(request: Request, asset_id: int, db: Session = Depends(get_db)):
    return templates.TemplateResponse("_agent.html", _agent_ctx(request, _get_asset(db, asset_id)))


@app.post("/assets/{asset_id}/agent/token", response_class=HTMLResponse)
def agent_new_token(request: Request, asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    token = ag.new_token()
    replaced = bool(asset.agent_token_hash)
    asset.agent_token_hash, asset.agent_last_seen, asset.agent_metrics = ag.token_hash(token), None, None
    db.add(AuditLog(asset_id=asset_id, action="AGENT_TOKEN_" + ("ROTATED" if replaced else "CREATED"),
                    new_value="Old agent disconnected" if replaced else "Agent token issued"))
    db.commit()
    return templates.TemplateResponse("_agent.html", _agent_ctx(request, asset, new_token=token))


@app.post("/assets/{asset_id}/agent/revoke", response_class=HTMLResponse)
def agent_revoke(request: Request, asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    asset.agent_token_hash = asset.agent_last_seen = asset.agent_metrics = None
    db.add(AuditLog(asset_id=asset_id, action="AGENT_REVOKED", new_value="Agent disconnected"))
    db.commit()
    return templates.TemplateResponse("_agent.html", _agent_ctx(request, asset))


@app.post("/api/agent/heartbeat")
async def agent_heartbeat(request: Request, db: Session = Depends(get_db)):
    authz = request.headers.get("authorization", "")
    if not authz.startswith("Bearer lat_"):
        return JSONResponse({"detail": "Missing agent token"}, status_code=401)
    body = await request.body()
    if len(body) > ag.MAX_PAYLOAD_BYTES:
        return JSONResponse({"detail": "Payload too large"}, status_code=413)
    asset = db.query(Asset).filter(Asset.agent_token_hash == ag.token_hash(authz[7:].strip())).first()
    if not asset:
        return JSONResponse({"detail": "Unknown or revoked token"}, status_code=403)
    try:
        payload = json.loads(body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError
    except ValueError:
        return JSONResponse({"detail": "Body must be a JSON object"}, status_code=400)
    was_offline = ag.status(asset.agent_last_seen) != "online"
    asset.agent_metrics = ag.clean_metrics(payload)
    asset.agent_last_seen = datetime.now(timezone.utc)
    if was_offline:
        db.add(AuditLog(asset_id=asset.id, action="AGENT_ONLINE",
                        new_value=f"Agent connected from {_client_ip(request)}"))
    db.commit()
    return {"ok": True, "interval": 30}


@app.get("/agent/lat-agent.py")
def agent_script():
    return FileResponse(APP_DIR / "static" / "lat-agent.py", media_type="text/x-python",
                        filename="lat-agent.py")


# ---------------------------------------------------------------- CSV export / import
EXPORT_COLUMNS = ["name", "category", "serial", "status", "location", "country", "region", "city", "datacentre",
                  "timezone", "notes", "created_at"]


def _csv_safe(v) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @ at the start of a cell)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@app.get("/export/csv")
def export_csv(request: Request, db: Session = Depends(get_db)):
    utz = tzs.get_tz(user_tz_name(request))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    for a in db.query(Asset).filter(Asset.archived_at.is_(None)).order_by(Asset.id).all():
        w.writerow([_csv_safe(x) for x in (a.name, a.category, a.serial_no, a.status, location_path(a.location),
                    a.country, a.region, a.city, a.site, a.timezone, a.notes, a.created_at.astimezone(utz).isoformat(timespec="minutes") if a.created_at else "")])
    fname = f"lean-asset-tracker-{datetime.now(utz):%Y%m%d-%H%M}.csv"
    return StreamingResponse(iter(["\ufeff" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


SYNONYMS = {
    "name": ["name", "asset", "asset name", "item", "hardware", "device", "label", "hostname"],
    "category": ["category", "type", "group", "class", "kind"],
    "serial_no": ["serial", "sn", "serial number", "serial_no", "s/n", "unique id"],
    "location": ["location", "site", "room", "place", "where", "rack"],
    "status": ["status", "state"],
    "notes": ["notes", "note", "comments", "description"],
    "timezone": ["timezone", "time zone", "tz"],
    "country": ["country", "nation", "country code"],
    "region": ["region", "state", "county", "province"],
    "city": ["city", "town"],
    "site": ["datacentre", "datacenter", "data centre", "data center", "dc", "facility", "provider"],
}


def _import_rows(db: Session, df: pd.DataFrame) -> tuple[int, int]:
    headers = {h.strip().lower().lstrip("\ufeff"): h for h in df.columns.astype(str)}
    mapping = {}
    for target, opts in SYNONYMS.items():
        for o in opts:
            if o in headers:
                mapping[target] = headers[o]
                break
    if "name" not in mapping:
        raise ValueError("No name column found. Use a header like Name, Device or Hardware.")

    def cell(row, key, default=None):
        col = mapping.get(key)
        if not col:
            return default
        v = row.get(col)
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return default
        s = str(v).strip()
        if s.startswith("'") and s[1:2] in ("=", "+", "-", "@"):
            s = s[1:]  # undo our own export escaping
        return s or default

    imported = skipped = 0
    seen = set()
    for _, row in df.iterrows():
        name = cell(row, "name")
        if not name:
            skipped += 1
            continue
        serial = cell(row, "serial_no")
        if serial and (serial in seen or db.query(Asset).filter(Asset.serial_no == serial).first()):
            skipped += 1
            continue
        if serial:
            seen.add(serial)
        status = (cell(row, "status", "active") or "active").lower()
        loc = cell(row, "location")
        category = (cell(row, "category", "General") or "General")[:80]
        if not serial:
            # No serial to match on: treat same name + category + location as the same device
            key = (name[:200], category, " / ".join(p.strip() for p in (loc or "").split("/") if p.strip()))
            if key in seen or any((a.name, a.category, location_path(a.location)) == key
                                  for a in db.query(Asset).filter(Asset.name == key[0], Asset.category == category)):
                skipped += 1
                continue
            seen.add(key)
        a = Asset(name=name[:200], category=category, serial_no=serial,
                  status=status if status in STATUSES else "active", notes=cell(row, "notes"),
                  timezone=tzs.valid_tz(cell(row, "timezone")) or geo.suggested_tz(geo.valid_country(cell(row, "country"))),
                  **_site_form_values(cell(row, "country", ""), cell(row, "region", ""), cell(row, "city", ""),
                                      cell(row, "site", "")),
                  location_id=ensure_location_path(db, loc) if loc else None)
        db.add(a)
        db.flush()
        db.add(AuditLog(asset_id=a.id, action="IMPORTED", new_value="Imported from CSV"))
        imported += 1
    db.commit()
    return imported, skipped


@app.post("/import/csv")
async def import_csv(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    wants_html = "text/html" in (request.headers.get("accept") or "")
    contents = await file.read()
    try:
        if len(contents) > 5 * 1024 * 1024:
            raise ValueError("File is over 5 MB.")
        df = pd.read_csv(io.BytesIO(contents), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        imported, skipped = _import_rows(db, df)
    except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as e:
        db.rollback()
        # UnicodeDecodeError subclasses ValueError, so test it first
        msg = ("That file doesn't look like a valid CSV." if isinstance(e, (UnicodeDecodeError, pd.errors.ParserError))
               else str(e))
        if wants_html:
            from urllib.parse import quote
            return RedirectResponse(f"/?import_error={quote(msg)}", status_code=303)
        return JSONResponse({"status": "error", "detail": msg}, status_code=400)
    if wants_html:
        return RedirectResponse(f"/?imported={imported}&skipped={skipped}", status_code=303)
    return {"status": "success", "imported": imported, "skipped": skipped}


# ---------------------------------------------------------------- JSON API (session-authenticated)
class MoveRequest(BaseModel):
    location_id: int


@app.get("/health")
def health():
    return {"status": "healthy", "version": VERSION}


@app.get("/api/assets")
def list_assets(db: Session = Depends(get_db)):
    return [{"id": a.id, "name": a.name, "category": a.category, "serial": a.serial_no, "status": a.status,
             "location": location_path(a.location), "agent": ag.status(a.agent_last_seen) if a.agent_token_hash else None,
             "archived": a.archived_at is not None}
            for a in db.query(Asset).order_by(Asset.id).all()]


@app.post("/assets/{asset_id}/move")
def move_asset(asset_id: int, req: MoveRequest, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    new_loc = db.query(Location).filter(Location.id == req.location_id).first()
    if not new_loc:
        raise HTTPException(404, "Target location not found")
    old = location_path(asset.location) or "No location"
    asset.location_id = req.location_id
    db.add(AuditLog(asset_id=asset_id, action="MOVE", old_value=old, new_value=location_path(new_loc)))
    db.commit()
    return {"status": "moved", "asset": asset.name, "to": location_path(new_loc)}


@app.get("/assets/{asset_id}/history")
def get_asset_history(asset_id: int, db: Session = Depends(get_db)):
    logs = db.query(AuditLog).filter(AuditLog.asset_id == asset_id).order_by(AuditLog.timestamp.desc()).all()
    return [{"timestamp": l.timestamp, "action": l.action, "old": l.old_value, "new": l.new_value} for l in logs]
