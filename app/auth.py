"""Authentication: scrypt password hashing, DB-backed sessions, login throttling.

Stdlib only (hashlib.scrypt, secrets, hmac). Session cookies hold a random token;
the DB stores only its SHA-256, so a leaked DB dump can't be replayed as a login.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import User, UserSession

log = logging.getLogger("lat.auth")

COOKIE = "lat_session"
SESSION_DAYS = 30
SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)
MIN_PASSWORD = 10

# Paths reachable without a session. Agent endpoints use their own bearer tokens.
PUBLIC_PREFIXES = ("/login", "/setup", "/health", "/api/agent/", "/agent/lat-agent.py")

# First-run setup token: from env, or generated and logged once at startup.
SETUP_TOKEN = os.environ.get("LAT_SETUP_TOKEN") or secrets.token_urlsafe(18)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                              n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(dk)))
        return hmac.compare_digest(test.hex(), dk)
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if password.lower() in {"password123", "changeme123", "qwertyuiop", "1234567890"}:
        return "That password is too common."
    return None


# Constant-ish timing for unknown emails so login doesn't reveal which accounts exist
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=sha256(token), user_id=user.id,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)))
    # opportunistic cleanup of expired sessions
    db.query(UserSession).filter(UserSession.expires_at < datetime.now(timezone.utc)).delete()
    db.commit()
    return token


def user_from_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    s = (db.query(UserSession).filter(UserSession.token_hash == sha256(token),
                                      UserSession.expires_at > datetime.now(timezone.utc)).first())
    return s.user if s else None


def destroy_session(db: Session, token: str | None) -> None:
    if token:
        db.query(UserSession).filter(UserSession.token_hash == sha256(token)).delete()
        db.commit()


class LoginThrottle:
    """In-memory: max `limit` failures per IP per `window` seconds."""

    def __init__(self, limit: int = 5, window: int = 900):
        self.limit, self.window, self.fails = limit, window, {}

    def blocked(self, ip: str) -> int:
        now = time.monotonic()
        hits = [t for t in self.fails.get(ip, []) if now - t < self.window]
        self.fails[ip] = hits
        return int(self.window - (now - hits[0])) if len(hits) >= self.limit else 0

    def fail(self, ip: str) -> None:
        self.fails.setdefault(ip, []).append(time.monotonic())

    def reset(self, ip: str) -> None:
        self.fails.pop(ip, None)


throttle = LoginThrottle()
