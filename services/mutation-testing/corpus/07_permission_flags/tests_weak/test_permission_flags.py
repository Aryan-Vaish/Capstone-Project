"""Weak suite -- intended tier: medium (55-70% expected kill).

Deliberately omitted: revoke and toggle entirely, and every case where AND and
OR would disagree -- so most bitwise mutations are expected to survive.
"""
from permission_flags import ADMIN, READ, WRITE, describe, grant, has_permission, is_admin


def test_has_permission_true():
    assert has_permission(READ, READ) is True


def test_has_permission_false():
    assert has_permission(READ, WRITE) is False


def test_grant_adds_flag():
    assert has_permission(grant(READ, WRITE), WRITE) is True


def test_is_admin():
    assert is_admin(ADMIN) is True


def test_describe_lists_read():
    assert "read" in describe(READ)
