"""User management: validation and the safety rules that stop an admin lockout.

Pure functions only — no DB, no FastAPI — so every rule here is unit-testable.
The routes in main.py do the I/O and call into this for every decision.

The rule that matters most: a self-hosted install has no password-reset email
and no support desk. If the last admin is demoted, deactivated or deleted,
nobody can ever administer the instance again. Every destructive path must go
through `lockout_problem()`.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

# Deliberately permissive: real addresses are odd, and we only ever send the
# invite link by hand. We reject what would break the UI or the DB column.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
MAX_EMAIL = 254
MAX_NAME = 80

INVITE_HOURS = 72
ROLES = ("admin", "member")

ROLE_HELP = {
    "admin": "Full access, plus managing people and invites.",
    "member": "Can add, edit, move and schedule devices. Cannot manage people.",
}


def normalise_email(raw: str) -> str:
    return (raw or "").strip().lower()


def email_problem(email: str) -> str | None:
    if not email:
        return "Enter an email address."
    if len(email) > MAX_EMAIL:
        return f"Email must be {MAX_EMAIL} characters or fewer."
    if not EMAIL_RE.match(email):
        return "That doesn't look like an email address."
    return None


def normalise_name(raw: str) -> str:
    return " ".join((raw or "").split())[:MAX_NAME]


def role_problem(role: str) -> str | None:
    return None if role in ROLES else "Pick a role from the list."


def is_admin_role(role: str) -> bool:
    return role == "admin"


def role_of(is_admin: bool) -> str:
    return "admin" if is_admin else "member"


def lockout_problem(action: str, *, target_is_admin: bool, target_is_active: bool,
                    other_active_admins: int) -> str | None:
    """Would this action leave the instance with no usable admin?

    `other_active_admins` counts active admins EXCLUDING the target. An action
    that removes the target's admin powers is only safe if someone else can
    still administer the instance.

    A target who is already inactive, or not an admin, carries no admin power,
    so removing them can never cause a lockout.
    """
    if not target_is_admin or not target_is_active:
        return None
    if other_active_admins > 0:
        return None
    verb = {"demote": "change the role of", "deactivate": "deactivate",
            "delete": "delete"}.get(action, action)
    return (f"You can't {verb} the only administrator — the instance would be "
            f"left with nobody who can manage it. Make someone else an admin first.")


def self_action_problem(action: str, *, is_self: bool) -> str | None:
    """Guard rails for acting on your own account from the admin list."""
    if not is_self:
        return None
    if action == "delete":
        return "You can't delete your own account from here."
    if action == "deactivate":
        return "You can't deactivate your own account — you'd be locked out immediately."
    return None


def new_invite_token() -> str:
    return secrets.token_urlsafe(32)


def invite_expiry(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) + timedelta(hours=INVITE_HOURS)


def invite_state(expires_at: datetime, accepted_at: datetime | None,
                 now: datetime | None = None) -> str:
    """-> 'accepted' | 'expired' | 'pending'."""
    if accepted_at is not None:
        return "accepted"
    now = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:                      # tolerate naive rows
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return "expired" if expires_at <= now else "pending"


def invite_url(base_url: str, token: str) -> str:
    return f"{(base_url or '').rstrip('/')}/invite/{token}"
