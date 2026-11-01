"""Public REST API — v1.

Why this exists: every comparable tool (Snipe-IT, Homebox, GLPI, AssetTiger)
has an API, and this audience automates everything. The common ask is not
"let me script CRUD" so much as "let me put my kit on my own dashboard" --
Homepage and Homarr both render custom JSON widgets -- so /summary is treated
as a first-class endpoint rather than an afterthought.

Design rules:

* Tokens are stored HASHED and shown exactly once, same as agent and invite
  tokens. A leaked database must not yield working credentials.
* Scopes are read or read-write. You can hand a wall-mounted dashboard a
  read-only token that physically cannot delete your inventory.
* This is new attack surface on an app people expose to their LAN, so auth
  failures are uniform, tokens are revocable, and every call is rate limited
  from the start rather than bolted on later.
* Responses are explicit dicts, never ORM objects, so adding a database
  column can never silently start leaking data (a password hash, say).
"""
import secrets
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from . import agent as ag
from .auth import sha256
from .database import get_db
from .models import ApiToken, Asset, AuditLog, Location

PREFIX = "lat_api_"
SCOPES = ("read", "write")

# Rate limit: generous for a dashboard polling every 30s, low enough that a
# stolen token cannot be used to scrape or brute force at speed.
RATE_LIMIT = 120           # requests
RATE_WINDOW = 60           # seconds
_hits: dict[str, deque] = defaultdict(deque)

router = APIRouter(prefix="/api/v1", tags=["v1"])


def new_token() -> str:
    return PREFIX + secrets.token_urlsafe(24)


def _rate_ok(key: str) -> bool:
    now = time.monotonic()
    q = _hits[key]
    while q and q[0] < now - RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return False
    q.append(now)
    return True


def _auth(authorization: str | None, db: Session, need: str) -> ApiToken:
    """Resolve a bearer token, or raise.

    The error text never distinguishes "no such token" from "revoked" from
    "wrong scope" beyond what the caller needs, so the endpoint cannot be used
    to probe which tokens exist.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Send 'Authorization: Bearer <token>'")
    raw = authorization[7:].strip()
    if not raw.startswith(PREFIX):
        raise HTTPException(401, "Invalid token")
    tok = db.query(ApiToken).filter(ApiToken.token_hash == sha256(raw)).first()
    if tok is None or tok.revoked_at is not None:
        raise HTTPException(401, "Invalid token")
    if not _rate_ok(tok.token_hash):
        raise HTTPException(429, f"Rate limit is {RATE_LIMIT} requests per {RATE_WINDOW}s")
    if need == "write" and tok.scope != "write":
        raise HTTPException(403, "This token is read-only")
    tok.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return tok


def read_token(authorization: str | None = Header(None), db: Session = Depends(get_db)):
    return _auth(authorization, db, "read")


def write_token(authorization: str | None = Header(None), db: Session = Depends(get_db)):
    return _auth(authorization, db, "write")


# ----------------------------------------------------------------- serialisers
def asset_json(a: Asset, loc_names: dict[int, str] | None = None) -> dict:
    """Explicit field list. Never dump the ORM object: a future column (an
    agent token hash, say) would start leaking the moment it was added."""
    status = ag.status(a.agent_last_seen) if a.agent_token_hash else None
    return {
        "id": a.id,
        "name": a.name,
        "category": a.category,
        "serial_no": a.serial_no,
        "status": a.status,
        "archived": a.archived_at is not None,
        "location_id": a.location_id,
        "location": (loc_names or {}).get(a.location_id),
        "site": a.site,
        "country": a.country,
        "region": a.region,
        "city": a.city,
        "timezone": a.timezone,
        "warranty_expires": a.warranty_expires.isoformat() if a.warranty_expires else None,
        "eol_date": a.eol_date.isoformat() if a.eol_date else None,
        "alerts_muted": bool(a.alerts_muted),
        "notes": a.notes,
        "agent": {
            "connected": bool(a.agent_token_hash),
            "state": status,
            "last_seen": a.agent_last_seen.isoformat() if a.agent_last_seen else None,
            "metrics": a.agent_metrics,
        },
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _loc_names(db: Session) -> dict[int, str]:
    return {l.id: l.name for l in db.query(Location).all()}


# ----------------------------------------------------------------- the widget
@router.get("/summary")
def summary(db: Session = Depends(get_db), _=Depends(read_token)):
    """One call, everything a dashboard widget needs.

    Exists because the realistic reason someone wants this API is to show
    their kit on Homepage/Homarr/Grafana without opening our UI. Keeping it a
    single flat-ish response means a widget needs no client-side maths.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    assets = db.query(Asset).filter(Asset.archived_at.is_(None)).all()
    online = offline = no_agent = 0
    for a in assets:
        if not a.agent_token_hash:
            no_agent += 1
        elif ag.status(a.agent_last_seen) == "online":
            online += 1
        else:
            offline += 1
    soon = [a for a in assets if a.warranty_expires and
            today <= a.warranty_expires <= today + timedelta(days=60)]
    eol = [a for a in assets if a.eol_date and
           today <= a.eol_date <= today + timedelta(days=180)]
    return {
        "generated_at": now.isoformat(),
        "assets": {"total": len(assets), "online": online,
                   "offline": offline, "no_agent": no_agent},
        "warranty_expiring_60d": [{"id": a.id, "name": a.name,
                                   "expires": a.warranty_expires.isoformat()} for a in soon],
        "eol_180d": [{"id": a.id, "name": a.name,
                      "date": a.eol_date.isoformat()} for a in eol],
        "counts": {"warranty_expiring_60d": len(soon), "eol_180d": len(eol),
                   "locations": db.query(Location).count()},
    }


# ----------------------------------------------------------------- assets
@router.get("/assets")
def list_assets(db: Session = Depends(get_db), _=Depends(read_token),
                category: str | None = None, location_id: int | None = None,
                status: str | None = None, offline: bool | None = None,
                archived: bool = False,
                warranty_before: date | None = None,
                limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    q = db.query(Asset)
    q = q.filter(Asset.archived_at.isnot(None)) if archived else q.filter(Asset.archived_at.is_(None))
    if category:
        q = q.filter(Asset.category == category)
    if location_id is not None:
        q = q.filter(Asset.location_id == location_id)
    if status:
        q = q.filter(Asset.status == status)
    if warranty_before:
        q = q.filter(Asset.warranty_expires.isnot(None),
                     Asset.warranty_expires <= warranty_before)
    total = q.count()
    rows = q.order_by(Asset.id).offset(offset).limit(limit).all()
    if offline is not None:
        rows = [a for a in rows
                if bool(a.agent_token_hash) and
                (ag.status(a.agent_last_seen) != "online") == offline]
    names = _loc_names(db)
    return {"total": total, "limit": limit, "offset": offset,
            "assets": [asset_json(a, names) for a in rows]}


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db), _=Depends(read_token)):
    a = db.get(Asset, asset_id)
    if not a:
        raise HTTPException(404, "No such asset")
    return asset_json(a, _loc_names(db))


@router.post("/assets", status_code=201)
def create_asset(body: dict, db: Session = Depends(get_db), tok=Depends(write_token)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "'name' is required")
    if body.get("serial_no"):
        clash = db.query(Asset).filter(Asset.serial_no == body["serial_no"]).first()
        if clash:
            raise HTTPException(409, f"Serial already used by asset {clash.id}")
    a = Asset(name=name[:200], category=(body.get("category") or "other")[:80],
              serial_no=(body.get("serial_no") or None), status=body.get("status") or "active",
              location_id=body.get("location_id"), notes=body.get("notes"),
              site=body.get("site"), country=body.get("country"),
              region=body.get("region"), city=body.get("city"),
              timezone=body.get("timezone"))
    for f in ("warranty_expires", "eol_date"):
        if body.get(f):
            try:
                setattr(a, f, date.fromisoformat(body[f]))
            except ValueError:
                raise HTTPException(422, f"'{f}' must be YYYY-MM-DD") from None
    db.add(a)
    db.commit()
    db.refresh(a)
    db.add(AuditLog(asset_id=a.id, action="CREATE",
                    new_value=f"Created via API token '{tok.name}'"))
    db.commit()
    return asset_json(a, _loc_names(db))


@router.patch("/assets/{asset_id}")
def update_asset(asset_id: int, body: dict, db: Session = Depends(get_db),
                 tok=Depends(write_token)):
    a = db.get(Asset, asset_id)
    if not a:
        raise HTTPException(404, "No such asset")
    allowed = {"name", "category", "serial_no", "status", "location_id", "notes",
               "site", "country", "region", "city", "timezone", "alerts_muted"}
    changed = []
    for k, v in body.items():
        if k in ("warranty_expires", "eol_date"):
            try:
                setattr(a, k, date.fromisoformat(v) if v else None)
            except (ValueError, TypeError):
                raise HTTPException(422, f"'{k}' must be YYYY-MM-DD or null") from None
            changed.append(k)
        elif k in allowed:
            setattr(a, k, v)
            changed.append(k)
        else:
            raise HTTPException(422, f"'{k}' is not an editable field")
    if changed:
        db.add(AuditLog(asset_id=a.id, action="EDIT",
                        new_value=f"Changed {', '.join(changed)} via API token '{tok.name}'"))
    db.commit()
    db.refresh(a)
    return asset_json(a, _loc_names(db))


@router.post("/assets/{asset_id}/archive")
def archive_asset(asset_id: int, db: Session = Depends(get_db), tok=Depends(write_token)):
    """Archive, never hard delete. The API must not be the easy way to destroy
    history -- permanent deletion stays a deliberate act in the UI."""
    a = db.get(Asset, asset_id)
    if not a:
        raise HTTPException(404, "No such asset")
    if a.archived_at is None:
        a.archived_at = datetime.now(timezone.utc)
        db.add(AuditLog(asset_id=a.id, action="ARCHIVE",
                        new_value=f"Archived via API token '{tok.name}'"))
        db.commit()
    return asset_json(a, _loc_names(db))


@router.get("/assets/{asset_id}/history")
def asset_history(asset_id: int, db: Session = Depends(get_db), _=Depends(read_token),
                  limit: int = Query(100, ge=1, le=500)):
    if not db.get(Asset, asset_id):
        raise HTTPException(404, "No such asset")
    rows = (db.query(AuditLog).filter(AuditLog.asset_id == asset_id)
            .order_by(AuditLog.id.desc()).limit(limit).all())
    return {"asset_id": asset_id, "events": [
        {"id": r.id, "action": r.action, "old": r.old_value, "new": r.new_value,
         # NB: the column is `timestamp`, not `created_at` -- using the wrong
         # one here 500s every history call.
         "at": r.timestamp.isoformat() if r.timestamp else None} for r in rows]}


@router.post("/assets/{asset_id}/history", status_code=201)
def add_note(asset_id: int, body: dict, db: Session = Depends(get_db),
             tok=Depends(write_token)):
    """Log something you did, from the terminal you were already in."""
    a = db.get(Asset, asset_id)
    if not a:
        raise HTTPException(404, "No such asset")
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(422, "'note' is required")
    row = AuditLog(asset_id=a.id, action="NOTE",
                   new_value=f"{note[:1000]} (via API token '{tok.name}')")
    db.add(row)
    db.commit()
    return {"ok": True, "asset_id": a.id, "note": note[:1000]}


@router.get("/locations")
def list_locations(db: Session = Depends(get_db), _=Depends(read_token)):
    rows = db.query(Location).order_by(Location.id).all()
    return {"locations": [{"id": l.id, "name": l.name, "path": l.path,
                           "parent_id": l.parent_id} for l in rows]}
