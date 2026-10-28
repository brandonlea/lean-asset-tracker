"""Make `pytest` work straight after `git clone`.

app.database raises at import time if DATABASE_URL is unset, which is right for
the real app but turns a contributor's first `pytest` run into a confusing
RuntimeError. The unit tests never open a connection, so a placeholder DSN is
enough to let the modules import.

A real DATABASE_URL in the environment always wins, so this can't mask the
setting you actually meant to use.

## Integration fixtures

`db_session`, `client_admin` and `client_member` need a live Postgres with the
`vector` extension (Asset carries a pgvector column, so SQLite cannot stand
in). Point TEST_DATABASE_URL at a throwaway database and they run; leave it
unset and they skip with a message rather than failing.

    docker run -d --rm --name lat-test -p 55433:5432 \
      -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=test \
      pgvector/pgvector:pg16
    TEST_DATABASE_URL=postgresql://test:test@127.0.0.1:55433/test pytest

These exist because the suite was previously unit-only: 141 tests passed while
every template was broken, because not one test rendered a template. Anything
that touches a route belongs here.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://unused:***@127.0.0.1:1/unused")
# Keep the in-app dispatcher loop out of the test process.
os.environ.setdefault("ALERT_INTERVAL_MIN", "0")

TEST_DB = os.environ.get("TEST_DATABASE_URL")
# This MUST happen at conftest import time, before anything imports
# app.database -- that module builds its engine from DATABASE_URL on import, so
# setting it inside a fixture is too late and every route test then dials the
# placeholder DSN on port 1.
if TEST_DB:
    os.environ["DATABASE_URL"] = TEST_DB


@pytest.fixture(scope="session")
def _engine():
    if not TEST_DB:
        pytest.skip("set TEST_DATABASE_URL for integration tests")
    os.environ["DATABASE_URL"] = TEST_DB
    from sqlalchemy import create_engine, text
    eng = create_engine(TEST_DB, future=True)
    with eng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.commit()
    from app.models import Base
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db_session(_engine):
    """A clean database per test. Truncate rather than recreate: far quicker."""
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    tables = ["alerts_sent", "alert_channels", "audit_log", "maintenance",
              "user_sessions", "invites", "assets", "locations", "users"]
    with _engine.connect() as c:
        for t in tables:
            c.execute(text(f"TRUNCATE TABLE {t} CASCADE"))
        c.commit()
    s = sessionmaker(bind=_engine, future=True)()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _client_as(db_session, *, is_admin: bool):
    """A TestClient carrying a genuine session cookie for a real user row.

    Deliberately not a monkeypatched auth bypass -- the login middleware is
    exactly the thing most likely to break, so the tests go through it.
    """
    from fastapi.testclient import TestClient
    from app import auth
    from app.main import app
    from app.models import User, UserSession

    u = User(email=f"{'admin' if is_admin else 'member'}@test.local",
             password_hash=auth.hash_password("correct horse battery staple"),
             is_admin=is_admin, is_active=True)
    db_session.add(u)
    db_session.commit()

    token = secrets.token_urlsafe(32)
    db_session.add(UserSession(token_hash=auth.sha256(token), user_id=u.id,
                               expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    db_session.commit()

    c = TestClient(app)
    c.cookies.set(auth.COOKIE, token)
    return c


@pytest.fixture
def client_admin(db_session):
    return _client_as(db_session, is_admin=True)


@pytest.fixture
def client_member(db_session):
    return _client_as(db_session, is_admin=False)
