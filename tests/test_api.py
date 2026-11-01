"""The public REST API.

Heaviest emphasis on authentication and scope, because this is new attack
surface on an app people expose to their LAN. The serialiser tests exist
because dumping an ORM object would leak whatever column someone adds next.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app import api as api_v1
from app.models import ApiToken, Asset, AuditLog, Location


def _mk_token(db, scope="read", name="test", revoked=False):
    raw = api_v1.new_token()
    from app.auth import sha256
    t = ApiToken(name=name, token_hash=sha256(raw), scope=scope,
                 revoked_at=datetime.now(timezone.utc) if revoked else None)
    db.add(t)
    db.commit()
    return raw


def _h(raw):
    return {"Authorization": f"Bearer {raw}"}


def _asset(db, **kw):
    kw.setdefault("name", "nas")
    kw.setdefault("category", "server")
    kw.setdefault("status", "active")
    a = Asset(**kw)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


# --------------------------------------------------------------------- authn
def test_no_token_is_rejected(client_admin):
    r = client_admin.get("/api/v1/summary")
    assert r.status_code == 401


def test_garbage_tokens_are_rejected(client_admin):
    for bad in ["", "Bearer", "Bearer junk", "Bearer lat_api_nope",
                "Basic abc", "lat_api_missing_bearer_prefix"]:
        r = client_admin.get("/api/v1/summary", headers={"Authorization": bad})
        assert r.status_code == 401, f"{bad!r} was accepted"


def test_a_revoked_token_stops_working(client_admin, db_session):
    raw = _mk_token(db_session, revoked=True)
    assert client_admin.get("/api/v1/summary", headers=_h(raw)).status_code == 401


def test_session_cookie_alone_cannot_use_the_api(client_admin):
    """Browser login must not grant API access -- CSRF would follow."""
    assert client_admin.get("/api/v1/assets").status_code == 401


def test_tokens_are_stored_hashed_never_in_clear(db_session):
    raw = _mk_token(db_session)
    rows = db_session.query(ApiToken).all()
    assert rows and all(r.token_hash != raw for r in rows)
    assert all(raw not in (r.token_hash or "") for r in rows)


def test_using_a_token_records_last_used(client_admin, db_session):
    raw = _mk_token(db_session)
    r = client_admin.get("/api/v1/summary", headers=_h(raw))
    assert r.status_code == 200, r.text
    # Read on a FRESH connection. The request ran on its own session and the
    # fixture session's identity map is not reliably invalidated by refresh()
    # or expire_all() once other test modules have run -- this test passed
    # alone and per-file but failed in the full suite until queried directly.
    # The behaviour under test is "the column gets written", so ask the
    # database, not the ORM cache.
    import os
    from sqlalchemy import create_engine, text
    eng = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        with eng.connect() as c:
            seen = c.execute(text(
                "select last_used_at from api_tokens order by id desc limit 1")).scalar()
    finally:
        eng.dispose()
    assert seen is not None, "last_used_at was not written"


# --------------------------------------------------------------------- scopes
def test_read_token_cannot_write(client_admin, db_session):
    raw = _mk_token(db_session, scope="read")
    a = _asset(db_session)
    assert client_admin.post("/api/v1/assets", json={"name": "x"},
                             headers=_h(raw)).status_code == 403
    assert client_admin.patch(f"/api/v1/assets/{a.id}", json={"name": "y"},
                              headers=_h(raw)).status_code == 403
    assert client_admin.post(f"/api/v1/assets/{a.id}/archive",
                             headers=_h(raw)).status_code == 403


def test_write_token_can_write(client_admin, db_session):
    raw = _mk_token(db_session, scope="write")
    r = client_admin.post("/api/v1/assets", json={"name": "pve01", "category": "server"},
                          headers=_h(raw))
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "pve01"


def test_a_read_token_still_reads(client_admin, db_session):
    raw = _mk_token(db_session, scope="read")
    _asset(db_session)
    assert client_admin.get("/api/v1/assets", headers=_h(raw)).status_code == 200


# ------------------------------------------------------------- no data leaks
def test_asset_json_never_exposes_secrets(db_session):
    """Guards against someone later returning the ORM object wholesale."""
    a = _asset(db_session, agent_token_hash="SECRETHASH")
    out = api_v1.asset_json(a)
    flat = repr(out)
    assert "SECRETHASH" not in flat
    assert "agent_token_hash" not in out
    assert out["agent"]["connected"] is True


def test_asset_json_is_an_explicit_allowlist(db_session):
    a = _asset(db_session)
    out = api_v1.asset_json(a)
    # If this fails someone changed the shape; update deliberately, with docs.
    assert set(out) == {
        "id", "name", "category", "serial_no", "status", "archived", "location_id",
        "location", "site", "country", "region", "city", "timezone",
        "warranty_expires", "eol_date", "alerts_muted", "notes", "agent", "created_at"}


# ------------------------------------------------------------ the widget call
def test_summary_shape_is_dashboard_ready(client_admin, db_session):
    raw = _mk_token(db_session)
    now = datetime.now(timezone.utc)
    _asset(db_session, name="online-box", agent_token_hash="h1", agent_last_seen=now)
    _asset(db_session, name="dead-box", agent_token_hash="h2",
           agent_last_seen=now - timedelta(hours=3))
    _asset(db_session, name="no-agent-box")
    _asset(db_session, name="warranty-soon",
           warranty_expires=date.today() + timedelta(days=30))
    r = client_admin.get("/api/v1/summary", headers=_h(raw))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["assets"]["total"] == 4
    assert d["assets"]["online"] == 1
    assert d["assets"]["offline"] == 1
    assert d["assets"]["no_agent"] == 2
    assert d["counts"]["warranty_expiring_60d"] == 1
    assert d["warranty_expiring_60d"][0]["name"] == "warranty-soon"


def test_summary_excludes_archived(client_admin, db_session):
    raw = _mk_token(db_session)
    _asset(db_session, name="live")
    _asset(db_session, name="gone", archived_at=datetime.now(timezone.utc))
    assert client_admin.get("/api/v1/summary", headers=_h(raw)).json()["assets"]["total"] == 1


def test_summary_on_an_empty_install_does_not_explode(client_admin, db_session):
    raw = _mk_token(db_session)
    d = client_admin.get("/api/v1/summary", headers=_h(raw)).json()
    assert d["assets"]["total"] == 0 and d["counts"]["warranty_expiring_60d"] == 0


# ----------------------------------------------------------------- filtering
def test_filters_work(client_admin, db_session):
    raw = _mk_token(db_session)
    loc = Location(name="Rack A", path="Home/Rack A")
    db_session.add(loc)
    db_session.commit()
    _asset(db_session, name="a", category="server", location_id=loc.id)
    _asset(db_session, name="b", category="switch")
    g = lambda qs: client_admin.get(f"/api/v1/assets{qs}", headers=_h(raw)).json()  # noqa: E731
    assert len(g("")["assets"]) == 2
    assert len(g("?category=switch")["assets"]) == 1
    assert len(g(f"?location_id={loc.id}")["assets"]) == 1
    assert g(f"?location_id={loc.id}")["assets"][0]["location"] == "Rack A"


def test_warranty_before_filter(client_admin, db_session):
    raw = _mk_token(db_session)
    _asset(db_session, name="old", warranty_expires=date(2026, 1, 1))
    _asset(db_session, name="new", warranty_expires=date(2030, 1, 1))
    d = client_admin.get("/api/v1/assets?warranty_before=2027-01-01", headers=_h(raw)).json()
    assert [a["name"] for a in d["assets"]] == ["old"]


def test_archived_are_hidden_unless_asked_for(client_admin, db_session):
    raw = _mk_token(db_session)
    _asset(db_session, name="gone", archived_at=datetime.now(timezone.utc))
    assert client_admin.get("/api/v1/assets", headers=_h(raw)).json()["total"] == 0
    assert client_admin.get("/api/v1/assets?archived=true", headers=_h(raw)).json()["total"] == 1


def test_limit_is_capped(client_admin, db_session):
    raw = _mk_token(db_session)
    assert client_admin.get("/api/v1/assets?limit=99999", headers=_h(raw)).status_code == 422


# ------------------------------------------------------------------- writing
def test_create_requires_a_name(client_admin, db_session):
    raw = _mk_token(db_session, scope="write")
    assert client_admin.post("/api/v1/assets", json={"category": "server"},
                             headers=_h(raw)).status_code == 422


def test_duplicate_serial_is_a_conflict_not_a_500(client_admin, db_session):
    raw = _mk_token(db_session, scope="write")
    _asset(db_session, name="first", serial_no="SN1")
    r = client_admin.post("/api/v1/assets", json={"name": "second", "serial_no": "SN1"},
                          headers=_h(raw))
    assert r.status_code == 409


def test_bad_date_is_422_not_500(client_admin, db_session):
    raw = _mk_token(db_session, scope="write")
    r = client_admin.post("/api/v1/assets",
                          json={"name": "x", "warranty_expires": "not-a-date"},
                          headers=_h(raw))
    assert r.status_code == 422


def test_patch_rejects_unknown_fields(client_admin, db_session):
    """Stops a typo silently doing nothing, and blocks writing to any column."""
    raw = _mk_token(db_session, scope="write")
    a = _asset(db_session)
    r = client_admin.patch(f"/api/v1/assets/{a.id}",
                           json={"agent_token_hash": "pwned"}, headers=_h(raw))
    assert r.status_code == 422


def test_writes_are_attributed_in_the_history(client_admin, db_session):
    """You must be able to see that a change came from a script, and which one."""
    raw = _mk_token(db_session, scope="write", name="provisioner")
    r = client_admin.post("/api/v1/assets", json={"name": "auto-box"}, headers=_h(raw))
    aid = r.json()["id"]
    logs = db_session.query(AuditLog).filter(AuditLog.asset_id == aid).all()
    assert logs and any("provisioner" in (l.new_value or "") for l in logs)


def test_archive_does_not_hard_delete(client_admin, db_session):
    raw = _mk_token(db_session, scope="write")
    a = _asset(db_session)
    assert client_admin.post(f"/api/v1/assets/{a.id}/archive", headers=_h(raw)).status_code == 200
    db_session.refresh(a)
    assert a.archived_at is not None
    assert db_session.get(Asset, a.id) is not None, "archive must never destroy the row"


def test_there_is_no_delete_endpoint(client_admin, db_session):
    """The API must not be the easy way to destroy history."""
    raw = _mk_token(db_session, scope="write")
    a = _asset(db_session)
    assert client_admin.delete(f"/api/v1/assets/{a.id}", headers=_h(raw)).status_code in (404, 405)


def test_add_note_appears_in_history(client_admin, db_session):
    raw = _mk_token(db_session, scope="write")
    a = _asset(db_session)
    assert client_admin.post(f"/api/v1/assets/{a.id}/history",
                             json={"note": "Replaced drive bay 3"},
                             headers=_h(raw)).status_code == 201
    r = client_admin.get(f"/api/v1/assets/{a.id}/history", headers=_h(raw))
    assert r.status_code == 200, r.text
    assert any("bay 3" in (e["new"] or "") for e in r.json()["events"])


def test_history_serialises_the_timestamp_column(client_admin, db_session):
    """Regression: the column is `timestamp`, not `created_at`. Getting this
    wrong 500s every history call and no unit test would notice."""
    raw = _mk_token(db_session, scope="write")
    a = _asset(db_session)
    db_session.add(AuditLog(asset_id=a.id, action="EDIT", new_value="x"))
    db_session.commit()
    r = client_admin.get(f"/api/v1/assets/{a.id}/history", headers=_h(raw))
    assert r.status_code == 200, r.text
    assert r.json()["events"][0]["at"] is not None


def test_missing_asset_is_404(client_admin, db_session):
    raw = _mk_token(db_session, scope="write")
    assert client_admin.get("/api/v1/assets/99999", headers=_h(raw)).status_code == 404
    assert client_admin.patch("/api/v1/assets/99999", json={"name": "x"},
                              headers=_h(raw)).status_code == 404


# ----------------------------------------------------------------- rate limit
def test_rate_limit_returns_429(client_admin, db_session, monkeypatch):
    raw = _mk_token(db_session)
    monkeypatch.setattr(api_v1, "RATE_LIMIT", 3)
    api_v1._hits.clear()
    codes = [client_admin.get("/api/v1/summary", headers=_h(raw)).status_code
             for _ in range(5)]
    assert 429 in codes, codes


# ------------------------------------------------------------------ token UI
def test_token_page_is_admin_only(client_member):
    assert client_member.get("/settings/api").status_code == 403


def test_token_is_shown_once_then_never_again(client_admin):
    r = client_admin.post("/settings/api", data={"name": "Dashboard", "scope": "read"})
    assert r.status_code == 200
    assert api_v1.PREFIX in r.text, "the new token must be displayed once"
    again = client_admin.get("/settings/api")
    assert api_v1.PREFIX not in again.text, "token must not be retrievable later"


def test_creating_a_token_requires_a_name(client_admin):
    r = client_admin.post("/settings/api", data={"name": " ", "scope": "read"})
    assert "name" in r.text.lower()


def test_bad_scope_is_refused(client_admin):
    r = client_admin.post("/settings/api", data={"name": "x", "scope": "superuser"})
    assert "read or write" in r.text.lower()


def test_revoking_through_the_ui_kills_the_token(client_admin, db_session):
    client_admin.post("/settings/api", data={"name": "Temp", "scope": "read"})
    t = db_session.query(ApiToken).first()
    client_admin.post(f"/settings/api/{t.id}/revoke")
    db_session.refresh(t)
    assert t.revoked_at is not None
