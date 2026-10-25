"""User management rules, especially the admin-lockout guard.

A self-hosted install has no password-reset email and no support desk. If the
last admin loses their powers, the instance is unadministrable forever. These
tests exist so that can never regress.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import users as u


# ---------------------------------------------------------------- email
@pytest.mark.parametrize("raw,expected", [
    ("  Brandon@Example.CO.UK ", "brandon@example.co.uk"),
    ("A@B.com", "a@b.com"),
    ("", ""),
])
def test_normalise_email(raw, expected):
    assert u.normalise_email(raw) == expected


@pytest.mark.parametrize("email", [
    "worker@example.com", "a.b+tag@sub.domain.co.uk", "x@y.zz",
])
def test_good_emails_accepted(email):
    assert u.email_problem(email) is None


@pytest.mark.parametrize("email", [
    "", "no-at-sign", "@nolocal.com", "spaces in@example.com",
    "trailing@dot.", "no@tld", "two@@at.com",
])
def test_bad_emails_rejected(email):
    assert u.email_problem(email) is not None


def test_overlong_email_rejected():
    assert u.email_problem("a" * 250 + "@example.com") is not None


# ---------------------------------------------------------------- name / role
def test_name_collapses_whitespace_and_truncates():
    assert u.normalise_name("  Jo   Bloggs \n") == "Jo Bloggs"
    assert len(u.normalise_name("x" * 200)) == u.MAX_NAME


def test_role_validation():
    assert u.role_problem("admin") is None
    assert u.role_problem("member") is None
    assert u.role_problem("superuser") is not None
    assert u.role_problem("") is not None


def test_role_round_trip():
    assert u.role_of(True) == "admin"
    assert u.role_of(False) == "member"
    assert u.is_admin_role(u.role_of(True))
    assert not u.is_admin_role(u.role_of(False))


# ---------------------------------------------------------------- lockout guard
@pytest.mark.parametrize("action", ["demote", "deactivate", "delete"])
def test_last_active_admin_is_protected(action):
    """The whole point: no path may strip the final admin of their powers."""
    problem = u.lockout_problem(action, target_is_admin=True,
                                target_is_active=True, other_active_admins=0)
    assert problem is not None
    assert "only administrator" in problem


@pytest.mark.parametrize("action", ["demote", "deactivate", "delete"])
def test_admin_removable_when_another_admin_remains(action):
    assert u.lockout_problem(action, target_is_admin=True, target_is_active=True,
                             other_active_admins=1) is None


def test_members_are_never_lockout_protected():
    for action in ("demote", "deactivate", "delete"):
        assert u.lockout_problem(action, target_is_admin=False,
                                 target_is_active=True, other_active_admins=0) is None


def test_already_inactive_admin_carries_no_power():
    """A deactivated admin can't log in, so deleting them can't cause a lockout."""
    assert u.lockout_problem("delete", target_is_admin=True, target_is_active=False,
                             other_active_admins=0) is None


# ---------------------------------------------------------------- self-action guard
def test_cannot_delete_or_deactivate_self():
    assert u.self_action_problem("delete", is_self=True) is not None
    assert u.self_action_problem("deactivate", is_self=True) is not None


def test_self_guard_ignores_other_people_and_other_actions():
    assert u.self_action_problem("delete", is_self=False) is None
    assert u.self_action_problem("deactivate", is_self=False) is None
    assert u.self_action_problem("demote", is_self=True) is None   # lockout guard covers this


# ---------------------------------------------------------------- invites
def test_invite_tokens_are_unguessable_and_unique():
    tokens = {u.new_invite_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 32 for t in tokens)


def test_invite_expiry_is_in_the_future_and_tz_aware():
    now = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)
    exp = u.invite_expiry(now)
    assert exp.tzinfo is not None
    assert exp == now + timedelta(hours=u.INVITE_HOURS)


def test_invite_state_transitions():
    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    future, past = now + timedelta(hours=1), now - timedelta(hours=1)

    assert u.invite_state(future, None, now) == "pending"
    assert u.invite_state(past, None, now) == "expired"
    # an accepted invite stays accepted even once its window has passed
    assert u.invite_state(past, past, now) == "accepted"
    assert u.invite_state(future, now, now) == "accepted"


def test_invite_state_tolerates_naive_timestamps():
    """Some drivers hand back naive datetimes; treat them as UTC, don't crash."""
    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    assert u.invite_state(datetime(2026, 10, 26, 13, 0), None, now) == "pending"
    assert u.invite_state(datetime(2026, 10, 26, 11, 0), None, now) == "expired"


def test_invite_expiring_exactly_now_is_expired():
    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    assert u.invite_state(now, None, now) == "expired"


def test_invite_url_has_no_double_slash():
    assert u.invite_url("https://lat.example/", "abc") == "https://lat.example/invite/abc"
    assert u.invite_url("https://lat.example", "abc") == "https://lat.example/invite/abc"
